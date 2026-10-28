"""#10017 phase 2 — COPY the legacy graph turns into the record.

The owner approved copy → verify → delete (2026-10-26). Three tools, one
population rule, no two of them allowed to disagree about what a turn is:

* ``tools/backfill_graph_turns.py`` — the **census** (read-only): how many turns
  per session, and what is anomalous. Landed, #10031.
* this module — the **copy** (additive, re-runnable): file each accepted turn
  into the record, positioned by the graph's own journal.
* ``tools/turn_parity_census.py`` — the **parity gate** (read-only): compares
  graph against record, per session, including embeddings, and classifies what
  may NOT be deleted. It is the authority for the DELETE decision; this phase
  only makes a comparison possible.

⛔ Why this is not `_emit_event`: that also writes the graph ``:GraphEvent``
store (``tortoise/sdk.py``, #432), so a copy routed through it would mint new
nodes into the very graph being emptied. The events here are plain dicts handed
to the record seam — the record is a CONSUMER of those dicts, so it is never
re-derived or INSERTed by hand.

⛔ Why the copy must not report success on a write it did not commit: the record
seam *never* commits. ``record_writer``'s own contract (module docstring, and
the live door at ``write_live_record``) is that the caller commits the
connection the seam returns. A copy that ignores the outcome and the connection
writes nothing, prints the number of turns it "copied", and exits 0 — the exact
silent-success shape that would let a delete step run against an empty record.

Population: identical to the census — an id of ``<session_id>_t<ascii digits>``
wired by ``CONTAINS`` to a session. Anything the rule cannot place is REPORTED
under its own class and NOT copied: an unkeyable node filed into the record is a
row nothing can compare against. A point wired to SEVERAL sessions is copied
under the session its id derives (which is what the census counts), and
reported, because ``tools/turn_parity_census.py`` fails any session that has a
graph turn and no record row — a node this phase refused to copy would fail that
gate permanently, with no way to satisfy it.

The ORPHAN class cannot be reached through the turn read at all (it starts at
``(s:Session)-[:CONTAINS]->``, so an unwired point is not in it), so it is
MEASURED by its own leg — the census's ``_ORPHANS_PAGE_QUERY`` /
``_ORPHANS_COUNT_QUERY``, re-used rather than restated. ⛔ Without that read the
class was structurally always zero and the artifact printed ``orphan: 0`` in the
measured slot while the delete sweep (``MATCH (p:Point) WHERE p.is_episodic =
true``, no kind filter) carried those nodes: a not-measured value read as
"measured: none", from the one tool whose artifact an operator reads before an
irreversible delete. Both sibling gates already treat these nodes as a refusal,
so a false zero here also made ``copy && parity-gate`` disagree about one graph.

EXIT CODES — the same contract the two sibling gates keep, so a chain
(``copy && parity-gate``) is not told "the backfill completed" by a run that
left nodes the record can never hold:

* ``0``  accepted nodes copied; every anomaly class is either zero or
  copied-and-reported.
* ``1``  the tool cannot START: an environment variable it was pointed at is not
  set, a flag received a pasted URI/DSN, the ``--journal`` FLAG is missing on a
  run that would write, or argparse rejected the invocation. This is why `main`
  uses ``_UsageParser``: argparse's default is ``2``, which would make a typo
  indistinguishable from a refusal. A ``--journal`` that was GIVEN but does not
  name an existing, non-empty file is not this case — the tool started and
  refused to guess, so it is ``2`` (below).
* ``2``  REFUSED. Either a BLOCKING anomaly class is nonzero (``off_shape``,
  ``orphan``, ``session_mismatch``, ``duplicate_index`` — no key the record can
  store), or the graph holds NO ``:Session`` nodes at all (the census's own
  POSITIVE CONTROL: every total is then a zero, which is what a wrong
  ``--graph`` — or a label/property shape that no longer matches — looks like
  too, and an artifact that cannot be told apart from "nothing to migrate" must
  not reach the delete phase as evidence), or the copy stopped on a write it
  could not prove: a truncated read, an
  unproven journal position, a non-contiguous segment, an append that throws
  (the journal's own ``OSError`` — ENOSPC/EIO/short write, so no position can be
  proven), a record that was not armed, a record that executed statements nobody
  committed, a commit that throws (the driver's own ``psycopg2.Error`` — a
  connection that died on the commit round trip), or the record seam itself raising
  (``RecordWriteError``/``RecordConnectError`` — unreachable, refusing, or
  rolling back at session N of M), or a ``--journal`` that does not name an
  existing, non-empty file (positions are per ``(org_id, graph_id)``; a fresh
  file would collide with live history and be swallowed by ``ON CONFLICT``).
  A blocking class is reported IN the artifact (the copy ran, so the JSON says
  what did land), and so is a refusal that lands part of the copy (a write whose
  position could not be proven at session N of M — the artifact carries the
  sessions that completed). A refusal raised before the loop (a duplicate turn
  key, a truncated read, a graph holding no ``:Session`` nodes, a ``--journal``
  that does not name an existing, non-empty file — a SHAPE check, not an
  identity one) writes no artifact,
  because nothing was copied.

Honest bounds, stated rather than implied:

* Memory: turn payloads (embeddings included) are materialised for the whole
graph before the emit loop, so peak cost scales with the tenant — hundreds of
MB at the largest production graph (6,432 turns), not a constant.
* Not atomic across sessions: one record connection per session means a
  per-session transaction, which is the right re-runnable boundary but is NOT a
  single atomic copy. An interrupted run leaves earlier sessions copied.
* Re-runnable for the RECORD, not for the JOURNAL. The ``point``/``session``
  keys are ``(org_id, graph_id, node_key)`` and every write is an absolute-value
  upsert, so a retry converges. The journal append is the durable commit point
  (``record_writer`` §17.1): a run that refuses AFTER appending leaves those
  events in the graph's own journal, and a retry appends every one of them
  again with fresh event ids. The record stays correct; the journal's replay
  truth set carries duplicates. There is no idempotency key on the append, so
  this phase cannot dedupe a retry — it is a recorded bound (#10017).
* The record's own counts are READ before the loop (``_record_floor``), because
  the upsert is absolute and `max()` over the graph alone can walk a value the
  record already holds DOWN: one extra connection and one query per run, and a
  read that cannot be made REFUSES rather than copying blind. A ``--dry-run``
  measures neither journal positions nor the floor (it opens no record
  connection at all), so its counts are the graph-side numbers the copy WOULD
  write, not the clamped ones.
  ⛔ THE FLOOR IS A SNAPSHOT, and its staleness window is STATED here because
  it cannot be closed from this side. It is read once, before the first journal
  append, and then threaded into every session's write; the upsert that could
  lower a value is the projection's own (``DO UPDATE SET turn_count =
  EXCLUDED.turn_count``), so closing the window would require the floor read to
  share the write's transaction — a seam change, not a knob here. What bounds
  it in practice is the precondition this tool already ENFORCES: a live writer
  appending to the journal during the run makes the copy's next append
  non-contiguous, which is refused (see the refusal list above), and a capture
  write reaches the record only after its own journal append. So the exposure
  is a capture write already IN FLIGHT when the run starts — its journal append
  behind the copy's first, its record commit ahead of the copy's write for that
  session — and no state the copy can read distinguishes it. The remedy shape is
  recorded on #10017: run the copy with the capture stopped and re-run it after
  any capture burst, because a re-run re-reads the floor and can only raise a
  count.
* The graph's own journal is the graph's REPLAY source. Appending ``PointAdded``
  to it means a later ``rebuild()`` re-creates the turns this migration deletes
  from the graph. That is a design consequence recorded on #10017, not a
  side effect to be discovered later.
* Every turn row this tool files carries ``cache_tier = 'record_only'``, so the
  RECORD-sourced rebuild does not re-materialise it (``read_record_view`` reads
  only ``cache_tier = 'graph'``). The tier is its own COLUMN statement inside
  each session's transaction, because ``_RECORD_ONLY_KEYS`` strips it from a
  ``PointAdded`` payload (#10070) — and the copy REFUSES if that statement
  reaches fewer rows than the turns it filed, so a run cannot report a clean
  copy whose rows the cache would node-ify. ``record_only_turns`` in the
  artifact is the number of committed rows it reached (``null`` on a dry run,
  which opens no record connection).
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import would fail first, and an UNATTRIBUTED failure is the harm
# #5128 was filed for. Same shape as the two siblings in this chain
# (tools/backfill_graph_turns.py, tools/turn_parity_census.py); the guard must be
# the first statement after the docstring and `import sys`, which
# `tests/test_entry_point_python_guard.py` pins for every tools/ entry point.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/backfill_copy_turns.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/backfill_copy_turns.py`"
    )

import argparse
import contextlib
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backfill_graph_turns as census  # same directory: ONE rule for "a turn"

#: Same predicate as the census's turn read, but carrying the payload — the copy
#: needs the properties, and it is paged for the same reason the census is.
_COPY_TURNS_PAGE_QUERY = (
    "MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE p.is_episodic = true "
    "AND id(p) >= $cursor "
    "WITH p, collect(s.id) AS sids "
    "RETURN id(p), p.id, properties(p), sids "
    "ORDER BY id(p) LIMIT $batch"
)
#: `properties(s)` and not three named fields: the session node carries the
#: capture provenance (`harness`, `capture_lane`, `model`, `machine_id`,
#: `source`, `actor_user_id`, `client_captured_at[_source]`,
#: `capture_redactions`), and once the graph is pruned this is the only place
#: it can still come from. A null `capture_redactions` is a MEANINGFUL value
#: ("no secret was ever redacted", #4911) — dropping the key would make the
#: record unable to falsify that reading.
#: PAGED for the same reason every other read here is: `RESULTSET_SIZE`
#: truncates a non-aggregate read silently, so an unpaged session read would
#: drop provenance on a large tenant and stop reporting `created_at_absent`
#: (the `sid in sessions` test would simply be false).
_COPY_SESSIONS_PAGE_QUERY = (
    "MATCH (s:Session) WHERE id(s) >= $cursor "
    "RETURN id(s), s.id, properties(s) "
    "ORDER BY id(s) LIMIT $batch"
)

#: Columns the copy OWNS — a graph property may never override these. The test
#: is on `record_projection.snake(prop)`, NOT on the spelling: the record snake-
#: cases every payload key into its column, so `turnCount` and `turn_count` are
#: the SAME column there and a raw filter would protect only one spelling.
_SESSION_DERIVED = ("turn_count", "turn_max_index")
#: Keys the envelope owns.
_SESSION_ENVELOPE = ("id", "event_id", "type", "ts", "initiated_by",
                     "projection_version", "contains_session")

#: The counts the RECORD already holds — the floor a copy may never lower.
#: `turn_count` is a COLUMN on both homes (`SessionRecorded` writes it to
#: `public.session` AND to `public.session_narrative`); `turn_max_index` names no
#: column, so it rides the `public.session` props TAIL and is read out of the
#: jsonb (a props key is stored VERBATIM — `record_projection._split_properties`).
_RECORD_FLOOR_SQL = (
    "SELECT node_key, turn_count, props->>'turn_max_index' "
    "FROM public.session WHERE org_id = %s AND graph_id = %s "
    "UNION ALL "
    "SELECT session_id, turn_count, NULL "
    "FROM public.session_narrative WHERE org_id = %s AND graph_id = %s"
)


#: The tier every turn row this tool files must carry (#10017 R2 / #10070).
#:
#: ⛔ IT CANNOT RIDE THE EVENT. `record_projection._RECORD_ONLY_KEYS` contains
#: `cache_tier`, so the key is stripped from the snapshot before
#: `_split_properties` ever sees it — the row then lands the schema's DEFAULT
#: `'graph'`, and `cache_projection.read_record_view` (whose point read is
#: `AND cache_tier = 'graph'`) hands every copied turn back to `record_plan`,
#: which plans a `MERGE` for each. An operator `record-restore` — the disaster
#: path this migration itself relies on — would then RE-CREATE the entire legacy
#: turn population the delete phase removes: the RAM the purge exists to reclaim
#: comes back, and the two rebuild routes produce two different graphs, which is
#: exactly what R2/step 1 (`"Blocks everything else"`) was ordered to prevent.
#: The migration's own acceptance checks stay green through it (the graph has 0
#: episodic points and the byte census reads correctly) until that first restore.
#:
#: #10070 shipped the column, the reader filter and the strip, and deferred the
#: WRITER to step 7's live carrier. This tool is a fourth writer and the only one
#: that creates the migrated population in bulk (~37.5k episodic points on the
#: recorded census), and its output is what the delete phase certifies — so it
#: states the tier itself, as its own COLUMN statement (#10070's named shape) in
#: the same transaction `emit` already commits.
#:
#: The predicate names `org_id`, `graph_id` and the row keys, and the record's RLS
#: policies scope the UPDATE to `app.current_org_id`, which `replay` sets on this
#: very transaction — so a WRONG TENANT matches zero rows rather than another
#: tenant's, and `_stamp_record_only_tier` refuses on a short rowcount instead of
#: certifying a placement that did not land.
_RECORD_ONLY_TIER_SQL = (
    "UPDATE public.point SET cache_tier = 'record_only' "
    "WHERE org_id = %s AND graph_id = %s AND node_key = ANY(%s)"
)


class CopyRefused(RuntimeError):
    """A copy that must not proceed, or whose write did not land."""


#: Anomaly classes whose nodes the RECORD CAN NEVER HOLD, so a nonzero count is
#: a refusal (exit 2) and not just a note: `off_shape`/`orphan` have no key the
#: record could store, `session_mismatch` would be filed under a session it does
#: not belong to, and `duplicate_index` cannot be represented at all. The
#: remaining classes are COPIES-and-reported: `cross_wired` shares one key,
#: `operator_flagged` and `not_point_kind_event` are nodes the record CAN hold —
#: and `tools/turn_parity_census.py` refuses on all three from its own side, so a
#: copy that reported zeros over them would be measuring a different population
#: than the gate it feeds.
_BLOCKING_ANOMALIES = ("off_shape", "orphan", "session_mismatch",
                       "duplicate_index")


class _UsageParser(argparse.ArgumentParser):
    """Usage errors exit ``1`` — ``2`` is reserved for a REFUSED copy.

    argparse's default is ``2``, which would make "you forgot --org-id"
    indistinguishable from "the record can never hold these nodes" to any caller
    keying on the exit code — the same rule ``tools/turn_parity_census.py``
    (``_UsageParser``) and ``tools/graph_census.py`` state.
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def classify_copy(rows) -> tuple[dict, dict]:
    """``(by_session, anomalies)`` from ``(internal_id, pid, props, sids)`` rows.

    ``by_session`` is ``{session_id: {index: props}}`` — index keyed, because the
    record's ``node_key`` IS ``<session>_t<index>`` and a duplicate index would
    collide on the record's primary key rather than fail loudly.
    """
    by_session: dict[str, dict[int, dict]] = {}
    anomalies: dict[str, list] = {
        "off_shape": [], "orphan": [], "cross_wired": [],
        "session_mismatch": [], "duplicate_index": [],
        "not_point_kind_event": [], "operator_flagged": [],
    }
    # `orphan` is NOT reachable from this function's rows (they start at
    # `(s:Session)-[:CONTAINS]->`, so a point with no session cannot be in them),
    # and this dict's zero is therefore NOT the artifact's answer: `copy_phase`
    # OVERWRITES it with the census's own orphan leg, measured. The branch below
    # is kept as the SHAPE GUARD it is — `sids` empty would otherwise reach
    # `sorted(sids)[0]` in the `session_mismatch` case and raise IndexError on a
    # row-shaped caller. Printing this zero in the artifact was the defect: an
    # unreachable branch is a not-measured slot, not a measured zero.
    for _internal_id, pid, props, sids in rows:
        props = dict(props or {})
        pid = "" if pid is None else str(pid)
        sids = [str(s) for s in (sids or ())]
        parsed = census._TURN_ID_RE.match(pid)
        index = census._int_or_none(parsed.group("index")) if parsed else None
        if parsed is None or index is None:
            anomalies["off_shape"].append(pid)
            continue
        derived = parsed.group("session")
        if not sids:
            anomalies["orphan"].append(pid)
            continue
        if len(sids) > 1:
            # COPIED under the derived session and reported: the census counts
            # this point as that session's turn, and the parity gate fails any
            # session holding a graph turn with no record row — refusing to
            # copy it would fail that gate forever, with nothing able to fix it.
            anomalies["cross_wired"].append({"id": pid, "sessions": sorted(sids)})
        # MEMBERSHIP, not `sids[0]`: `collect(s.id)` has no defined order, so
        # testing the first element let the same node be copied or dropped
        # depending on which edge the server returned first.
        if derived not in sids:
            anomalies["session_mismatch"].append(
                {"id": pid, "wired_session": sorted(sids)[0],
                 "derived_session": derived})
            continue
        if props.get("pointKind") != "event":
            # COPIED and reported: the id space places it and the record's turn
            # read does not require the kind, but the parity gate excludes these
            # from its purge set — so the divergence is measured here instead of
            # this phase quietly claiming a different population.
            anomalies["not_point_kind_event"].append(pid)
        if props.get("is_operator"):
            # COPIED and reported for the same reason, and this is the OTHER
            # half of the parity gate's population split: that gate refuses on
            # `operator_flagged` from its own side, so a copy artifact reporting
            # zeros over it would measure a different population than the gate it
            # feeds.
            anomalies["operator_flagged"].append(pid)
        bucket = by_session.setdefault(derived, {})
        if index in bucket:
            anomalies["duplicate_index"].append(
                {"session": derived, "index": index, "id": pid})
            continue
        bucket[index] = props
    return by_session, anomalies


def _record_floor(dsn: str, org_id: str, graph_id: str) -> dict[str, dict]:
    """The counts the record ALREADY holds, read once per run.

    ⛔ WHY THIS READ EXISTS. The record's upsert is ABSOLUTE —
    ``DO UPDATE SET turn_count = EXCLUDED.turn_count``, and the tail is
    ``props = session.props || EXCLUDED.props`` so EXCLUDED wins a collision. So
    ``max(declared, attached)`` protects against the GRAPH's own declaration and
    **cannot** protect against the RECORD's value: a record already holding 20
    (a capture whose record write landed while the graph's ``:Session`` MERGE did
    not — #9390 — or a graph restored from an older backup) is walked DOWN to the
    graph's 12 by a re-run, and the stale-turn sweep then reads a shorter range
    than the real one (``sdk`` reads ``coalesce(turn_max_index, turn_count, 0)``,
    so the surplus it exists to remove is never seen — #10000).

    Both homes are read because the same `SessionRecorded` sets `turn_count` on
    each (`record_projection._narrative_statements`), and a floor measured from
    one of them would leave the other free to go down.

    The read goes through `record_serve.open_record` and
    `record_projection.scope_connection` — the record's own bounded opener and its
    VERIFIED R6 tenant scope — because a bare connection reads ZERO rows on the
    app role and a zero-row read is a SUCCESSFUL read.

    A read that cannot be MADE refuses. An unmeasured floor is exactly the state
    this function exists to remove, so falling back to `{}` would silently
    restore the lowering write — the fail-open shape this whole phase is about.

    ⛔ WHAT THIS SNAPSHOT DOES NOT COVER. It is read ONCE, before the copy's
    first journal append, and a live capture that commits a `SessionRecorded`
    between this read and the copy reaching that session would leave the max()
    comparing against a stale floor. That window is stated, not implied, in the
    module docstring's "Honest bounds" — it cannot be closed here because the
    lowering upsert is the projection's own (`DO UPDATE SET turn_count =
    EXCLUDED.turn_count`), so closing it means reading inside the write's
    transaction. The practical precondition — run with the capture stopped — is
    the same one the journal-contiguity refusal already enforces for the rest of
    the run, and a re-run re-reads this floor and can only RAISE a count.
    """
    from tortoise import record_projection, record_serve
    from tortoise.record_projection import RecordProjectionError
    from tortoise.record_serve import RecordServeError
    from tortoise.record_writer import RecordWriteError

    try:
        # ⛔ THE SAME DOOR THE REST OF THE RECORD IS READ THROUGH, not a second
        # `psycopg2.connect` and not `RecordWriter.connect` (a dataclass FIELD,
        # `None` unless a test injects a hook — calling it is a TypeError, which
        # would make every real run refuse before copying a row). Two properties
        # live in these two calls:
        #
        # * `open_record` is the ONE bounded opener: it resolves the DSN through
        #   `record_writer.record_dsn` (so "armed" cannot mean two things), opens
        #   with the `CONNECT_TIMEOUT_SECONDS` bound, and sets a
        #   `statement_timeout` so a wedged database cannot hang the run.
        # * `scope_connection` installs AND VERIFIES the tenant scope (#9600).
        #   Without it this SELECT reads ZERO ROWS on the app role:
        #   `public.session` and `public.session_narrative` are RLS-enabled with an
        #   `org_id = current_setting('app.current_org_id', true)` policy, and an
        #   unset GUC makes the predicate NULL — so the rows are FILTERED, not an
        #   error. A zero-row read is a SUCCESSFUL read, so the empty floor would
        #   come back as "the record holds nothing" and the lowering write this
        #   function exists to prevent would go ahead, reported clean — the
        #   measured #9801 shape ("counted ZERO rows under the `*_app_tenant`
        #   policies, and reported the record as EMPTY").
        conn = record_serve.open_record(dsn)
        try:
            cursor = record_projection.scope_connection(conn, org_id)
            cursor.execute(_RECORD_FLOOR_SQL,
                           (org_id, graph_id, org_id, graph_id))
            rows = cursor.fetchall()
        finally:
            with contextlib.suppress(Exception):
                conn.close()
    except Exception as exc:
        # Our own seam's messages are DSN-free by construction (they name a class,
        # a label, or the missing extra), so they are quoted — the operator needs
        # "install the 'postgres' extra" and not "RecordServeError". A DRIVER's
        # message can carry the host, and this module never echoes a DSN (see the
        # `--dsn-env` shape check in `main`).
        #
        # ⛔ `RecordProjectionError` IS IN THIS TUPLE, and it is the one that
        # matters most. `scope_connection` is the call immediately above, and it
        # raises that class (`record_projection._apply_tenant_scope`) — NOT a
        # `RecordWriteError` and NOT a `RecordServeError` — for the two
        # conditions an operator is most likely to hit: a tenant GUC that did not
        # take, and a connection role that BYPASSES the policies. Reporting those
        # as the bare class name told the operator the record's DATA could not be
        # read while the actual message said to re-arm the DSN with the tenant
        # role (the #9801 shape this whole function exists to refuse). Its text
        # is DSN-free for the same reason the other two are: it names a GUC, a
        # role posture, or a missing extra — never a host.
        detail = (str(exc)
                  if isinstance(exc, (RecordWriteError, RecordServeError,
                                      RecordProjectionError))
                  else type(exc).__name__)
        raise CopyRefused(
            "the record's own counts could not be read, so a re-run cannot be "
            f"proven not to lower them ({detail}) — refusing rather than writing "
            "a value the record may already exceed") from exc
    floor: dict[str, dict] = {}
    for session_id, turn_count, turn_max_index in rows:
        held = floor.setdefault(str(session_id), {})
        count = census._int_or_none(turn_count)
        mark = census._int_or_none(turn_max_index)
        if count is not None:
            held["turn_count"] = max(held.get("turn_count", count), count)
        # ⛔ The record's OWN read spells the window
        # `coalesce(s.turn_max_index, s.turn_count, 0)` (`tortoise/sdk.py:3075`),
        # so a row that holds a COUNT but no mark still states a window of
        # `count`. Folding only the literal `turn_max_index` column made that row
        # read as "nothing held" at the write site (`or -1`), so a run whose graph
        # still attached `attached < count` turns wrote the SMALLER mark over the
        # record and moved its effective window DOWNWARDS (measured: a row at
        # `turn_count=20` with a NULL mark reads 20, and a copy with
        # `attached=11` wrote `turn_max_index=11`) — the exact lowering this
        # function exists to prevent, through the one spelling the read collapses.
        # `session_narrative` is the home that makes it reachable: it carries
        # `turn_count` and no `turn_max_index` column at all.
        effective_mark = mark if mark is not None else count
        if effective_mark is not None:
            held["turn_max_index"] = max(
                held.get("turn_max_index", effective_mark), effective_mark)
    return floor


def _column_keys(props: dict, column: str) -> list[str]:
    """Every key in ``props`` the RECORD would resolve into ``column``.

    THE one definition of "this key names that column", shared by all three of
    the copy's column guards (the ownership filter, the `created_at` null rule,
    and the absent-timestamp report). `record_projection._split_properties`
    resolves a key with `snake()`, so `turnCount`, `createdAt` and `CreatedAt`
    all land in the same columns their snake spellings do — a guard written
    against a literal list of spellings protects only the spellings its author
    thought of, and the three guards drifted apart exactly that way (three
    review cycles, one key each). Resolving here means they cannot drift again.
    """
    from tortoise.record_projection import snake as _record_snake
    return [k for k in props if _record_snake(k) == column]


def _declared(props: dict, column: str) -> int | None:
    """The graph's DECLARED value for one of the copy's own columns.

    Read by the RECORD's identity rule (`record_projection.snake`), not by a
    spelling: the same rule `build_session_event` filters the payload with, so
    the two cannot disagree about what a key means. Without it, the payload
    filter that drops a camelCase `turnCount` also drops it from the DECLARATION —
    the value then reaches neither the payload nor the three-term `max()`, and a
    camelCase session silently loses the graph's declaration from a contract the
    comment block above states absolutely (`max(declared, attached, floor)`).

    Two spellings of one column are MAXed, not last-wins: every number here is
    part of a guard against writing DOWN, and taking the smaller of two
    declarations would walk the record down through the very write the guard
    exists to prevent. `None` means the graph declared nothing this resolver can
    read.
    """
    found: int | None = None
    for prop in _column_keys(props, column):
        measured = census._int_or_none(props[prop])
        if measured is not None and (found is None or measured > found):
            found = measured
    return found


def build_session_event(session_id: str, props: dict, *, turn_count: int,
                        max_index: int, event_id: str,
                        floor: dict | None = None) -> dict:
    """``SessionRecorded`` — the record row's source, and the sweep's mark.

    ``floor`` is the record's own value for this session (`_record_floor`). It
    is folded into the same `max()` as the graph's, because the DB never sees
    the comparison: the upsert writes whatever this dict says over whatever the
    row holds.
    """
    # EVERY graph property is carried, minus the two the copy derives and minus
    # the envelope's keys. The record's OWN split (`_split_properties`) places a
    # property naming a column into that column and the rest into `props`, so a
    # whitelist here loses data silently — the version that had one dropped
    # `capture_ok`, `capture_extractor`, `entity_links_*`, `commit_count`,
    # `draft_count`, `value_nodes_*`, `updated_at` and the props tail, and
    # neither gate can see it (the census reads only the counts; the parity gate
    # compares turn nodes).
    # ⛔ DEFAULTS FIRST, PAYLOAD SECOND, ENVELOPE THIRD. The default used to be
    # written LAST (`event.update({… "is_episodic": props.get(…)})` after the
    # payload), so it won every collision: a `:Session` carrying `isEpisodic:
    # false` (camelCase) had its property forced to `True` by the snake_case
    # default beside it — both keys reach `_split_properties`, which snake-cases
    # them into the SAME column and lets the LAST one win. Defaults first means
    # a graph property always overrides the default, never the other way round.
    # ⛔ THE OWNERSHIP TEST IS SPELLING-INDEPENDENT, for the same reason the
    # `created_at` rule below is. The record does not identify a property by its
    # spelling: `record_projection._split_properties` runs every key through
    # `snake()` and places it in the column that name resolves to, so a graph
    # property spelled `turnCount` lands in the SAME `turn_count` column as
    # `turn_count` does — and with the omission rule active (no attached turns)
    # there is no derived value to win the max, so a raw spelling filter would
    # let the payload alone walk the record's count down. Measuring the
    # snake-cased name is what makes "a graph property may never override these"
    # true of the record's column and not merely of one of its spellings.
    # Measured through the shared resolver, so this filter and the `created_at`
    # rule below are the same rule and not two spellings of it.
    derived = [k for column in _SESSION_DERIVED
               for k in _column_keys(props, column)]
    event = {"is_episodic": True}
    event.update({k: v for k, v in props.items()
                  if k not in derived and k not in _SESSION_ENVELOPE})
    event.update({
        "event_id": event_id, "type": "SessionRecorded",
        "initiated_by": "backfill", "projection_version": 2,
        "id": session_id,
    })
    # A session with no `created_at` in the graph gets NO key — never a timestamp
    # (a backfilled `now` is indistinguishable from a real creation time), and
    # NOT a present null either: `created_at` is a record COLUMN and the upsert
    # is `DO UPDATE SET created_at = EXCLUDED.created_at`, so a present null
    # BLANKS a real time the record already holds on a re-run. Omitting the key
    # leaves an existing value alone and still writes NULL on a first insert.
    #
    # ⛔ The payload above is copied VERBATIM, so the rule is ENFORCED here, not
    # merely hoped for: a graph property naming this column with a null value
    # would otherwise reach `_split_properties`, which snake-cases it into
    # `col_values["created_at"] = None` with no value filter
    # (`record_projection._split_properties`) — the blanking write this paragraph
    # forbids, while `copy_phase` reported the same session under
    # `created_at_absent`. Popping EVERY spelling that names the column is what
    # makes the claim true of a present null and not only of an absent key.
    #
    # ⛔ EVERY SPELLING, not the two literals this started as. The record
    # resolves a key with `snake()`, so `CreatedAt` names the SAME column as
    # `created_at` and blanks it exactly as well; a literal tuple is a guard that
    # protects the spellings someone happened to think of. This is the same rule
    # the ownership filter above uses, so all three of the copy's column guards
    # now share one definition of "names the column".
    for named in _column_keys(event, "created_at"):
        if event[named] is None:
            event.pop(named)
    # ⛔ The record's bookkeeping NEVER goes DOWN. `turn_count` is a COLUMN and
    # `turn_max_index` rides the props tail; the record's upsert is
    # `ON CONFLICT … DO UPDATE SET` and its tail merge is
    # `props = session.props || EXCLUDED.props`, so a smaller number written
    # here OVERWRITES the truth. A `-1` is worse than leaving it out: `sdk`
    # reads `coalesce(s.turn_max_index, s.turn_count, 0)`, so -1 makes the
    # stale-turn sweep walk nothing at all (#10000 is exactly why it exists).
    #
    # WHEN THE COUNT IS WRITTEN: only when the graph MEASURED at least one
    # attached turn. Nothing below can be established from a declaration alone:
    #
    # * `max(declared, attached)` is still needed — the census measured 21
    #   sessions declaring FEWER turns than they carry (declared=6, attached=12),
    #   and the declaration would preserve the wrong number.
    # * But `max()` over the graph alone protects only against the graph's OWN
    #   declaration — it cannot protect against what the RECORD already holds, and
    #   `floor` above is what closes that: it is folded into the same `max()` for
    #   every write below. With `attached=0` the only
    #   number available IS the declaration — the one the census proved drifts
    #   LOW — so writing `max(declared, 0)` walks a real `turn_count=12` down to
    #   `6` through `DO UPDATE SET turn_count = EXCLUDED.turn_count`, and
    #   `turn_max_index` down through the props merge. Measured on the census's
    #   own population: run 1 writes `{"turn_count": 12, "turn_max_index": 11}`;
    #   the post-prune re-run (turn nodes gone, `declared=6`) writes `6`. That is
    #   the case this module's contract forbids, and it is reachable: the tool is
    #   re-runnable by design and the delete phase runs against the same graph.
    # * So the guard is the MEASUREMENT, not the declaration: `attached > 0`
    #   means the graph can still vouch for a window length, and `max()` against
    #   the floor then only ever raises it. With `attached == 0` the graph proves
    #   nothing about a
    #   window it no longer holds, so the keys are OMITTED — the column is not in
    #   the INSERT's column list, `DO UPDATE` never names it, an existing value
    #   survives (pinned through the record's own statement emitter below), and a
    #   first insert takes the column's own NULL: the honest "no count was ever
    #   stated", which is what `created_at` above already argues for.
    declared_count = _declared(props, "turn_count") or 0
    declared_max = _declared(props, "turn_max_index")
    # Each key is guarded by ITS OWN measurement: `turn_count > 0` is a count,
    # `max_index >= 0` is a high-water mark, and a caller that passes one without
    # the other must not be handed a synthetic `-1` (the value the block above
    # calls worse than absence). `copy_phase` can only produce the coupled pair,
    # so this guard is what makes the module-level contract true on its own.
    # THE FLOOR IS PART OF THE MAX, not a separate branch: three numbers can each
    # be the largest — what the graph declares, what the graph still attaches,
    # and what the record already holds — and the write must beat all three or it
    # goes down. `or 0` / `or -1` are the "nothing held" spelling, so an
    # unmeasured floor (a dry run, a first copy) changes nothing.
    if turn_count > 0:
        event["turn_count"] = max(declared_count, turn_count,
                                  (floor or {}).get("turn_count") or 0)
    if max_index >= 0:
        event["turn_max_index"] = max(
            declared_max if declared_max is not None else -1, max_index,
            (floor or {}).get("turn_max_index") or -1)
    return event


def build_turn_event(session_id: str, props: dict, *, event_id: str) -> dict:
    """``PointAdded`` for one turn, carrying its ``CONTAINS`` session linkage."""
    # Imported HERE, not at module scope: `tortoise.embeddings` pulls numpy, and
    # `--dry-run`/`--help` should not pay for it (the same reason `_journal_emit`
    # imports the record seam locally).
    from tortoise.embeddings import stamp_journal_embedding

    snap = {k: v for k, v in props.items() if k != "content_hash"}
    # Presence is ownership (#5004): an absent key lets replay invent a vector.
    snap.setdefault("embedding", None)
    # ⛔ THE SEAM, not a hand-rolled approximation of it.
    # `stamp_journal_embedding` is *"THE single seam both journal producers use …
    # so they cannot drift"* (`tortoise/embeddings.py`), and it exists because a
    # graph read-back is not JSON-shaped: `properties(p)` can hand back a driver
    # vector or a numpy array after a `vecf32` write (#5004 round-3). This tool
    # feeds exactly that read-back into a `json.dumps` payload
    # (`record_projection._journal_row`), where a non-list container raises and
    # aborts the run AFTER the journal append. The seam also refuses a
    # non-numeric / non-finite / implausibly long vector, dropping it to `None`
    # while the field stays OWNED.
    #
    # `creating=False` because this is a RE-EMITTED SNAPSHOT: the vector was
    # computed at capture time, possibly under a different model, and nothing in
    # the graph records which. Attesting the model that happens to be configured
    # on the machine running the copy would stamp a false origin — the exact
    # failure the seam's own `embedding_preserved` branch exists to prevent.
    # ⚠️ This departs from ONE line of #10017's recorded copy recipe
    # (`creating=True`), which was read off the SDK's CREATING emission site
    # where the vector IS computed in the same write; recorded here so the next
    # lane can see the choice rather than rediscover it.
    snap = stamp_journal_embedding(snap, creating=False)
    return {
        "event_id": event_id, "type": "PointAdded",
        "initiated_by": "backfill", "projection_version": 2,
        "id": props["id"], "point": snap, "contains_session": session_id,
    }


def copy_phase(graph, emit, *, page_size: int | None = None, event_id,
               record_floor: dict | None = None) -> dict:
    """File every accepted turn into the record. Additive, re-runnable.

    ``emit(session_id, events)`` is the only side effect. The PRODUCTION emitter
    never returns ``None``: an unproven journal position RAISES ``CopyRefused``
    (`position=False` does not omit the rows — it omits only the `journal_event`
    position row, so writing there would file unnumbered turns, #9190). ``None``
    is the DRY-RUN spelling only, meaning "no position was measured". A refusal
    carries the partial ``stats`` on the exception so the caller can still write
    an artifact saying what landed.
    """
    if page_size is None:
        page_size = census._PAGE_SIZE
    session_rows = list(census._paged_rows(graph, _COPY_SESSIONS_PAGE_QUERY,
                                           page_size))
    census._cross_check(":Session", len(session_rows),
                        census._count(graph, census._SESSIONS_COUNT_QUERY)[0])
    # ⛔ POSITIVE CONTROL, the census's own (`backfill_graph_turns.main`). With no
    # `:Session` nodes every total below is a zero, and a mistyped `--graph` (or a
    # label/property shape that no longer matches) produces the SAME all-zero
    # artifact as a graph with nothing to migrate. This phase's artifact is what
    # an operator reads as step 2 of a destructive migration, so it must not be
    # tellable-apart from "nothing to migrate": refuse, before anything is read
    # for writing and before anything is emitted. A zero-TURN graph that DOES hold
    # sessions stays green — that is a legitimate post-cutover shape.
    if not session_rows:
        raise CopyRefused(
            "this graph holds no :Session nodes, so every total this run "
            "would report is a zero — which is what a wrong --graph, or a "
            "label/property shape that no longer matches, looks like too. "
            "Refusing to hand the delete phase an artifact that cannot be told "
            "apart from 'nothing to migrate' (the census's own positive control)")
    sessions = {str(sid): dict(props or {})
                for _internal, sid, props in session_rows}
    rows = list(census._paged_rows(graph, _COPY_TURNS_PAGE_QUERY, page_size))
    census._cross_check("copy turn", len(rows),
                        census._count(graph, census._TURNS_COUNT_QUERY)[1])

    by_session, anomalies = classify_copy(rows)
    # ⛔ THE ORPHAN CLASS IS MEASURED BY ITS OWN LEG. The turn read starts at
    # `(s:Session)-[:CONTAINS]->p`, so an episodic point with NO session is not in
    # it and `classify_copy`'s `orphan` branch can never fire — reporting that
    # branch's zero put a NOT-MEASURED value in the artifact's measured slot.
    # The delete sweep (`MATCH (p:Point) WHERE p.is_episodic = true`, no kind
    # filter) does carry those nodes, and both sibling gates refuse them, so a
    # zero here would be the one number in this artifact an operator could not
    # act on. `orphan` is in `_BLOCKING_ANOMALIES`, so a nonzero count exits 2
    # below, naming the ids.
    orphan_ids = [str(r[1]) for r in census._paged_rows(
        graph, census._ORPHANS_PAGE_QUERY, page_size)]
    census._cross_check("orphan", len(orphan_ids),
                        census._count(graph, census._ORPHANS_COUNT_QUERY)[0])
    anomalies["orphan"] = orphan_ids
    # ⛔ A TURN WHOSE SESSION THE SESSION LEG DID NOT READ REFUSES THE COPY. The
    # session read is paged and cross-checked BEFORE the turn read runs, so a
    # session a live writer created in between appears only in `by_session` — and
    # the emit loop's `sessions.get(sid, {})` then hands `build_session_event` an
    # EMPTY dict: an id-only `SessionRecorded` is filed carrying none of the
    # capture provenance (`harness`, `capture_lane`, `capture_redactions`,
    # `created_at`), every anomaly class reads zero, `created_at_absent` misses it
    # (that report is gated on `sid in sessions`), and the run exits 0. Once the
    # graph is pruned that provenance is gone and nothing can see the hole —
    # `tools/turn_parity_census.py` compares TURNS. So this takes the remedy the
    # journal leg already takes for a concurrent writer: refuse, and say to
    # re-run with the writer stopped.
    unread = sorted(set(by_session) - set(sessions))
    if unread:
        raise CopyRefused(
            f"{len(unread)} session(s) own turns in this read but were not in the "
            f"session read: {', '.join(unread[:5])}"
            f"{' …' if len(unread) > 5 else ''} — their capture provenance cannot "
            "be copied, and filing them without it would lose it permanently. A "
            "live writer interleaved with the copy; re-run with it stopped")
    # ⛔ A DUPLICATE turn key REFUSES the copy. The record can hold one row per
    # ``node_key``, so the loser can never be filed — and
    # ``tools/turn_parity_census.py`` cannot represent two nodes under one key
    # either: it merges them and then reports the shortfall as a TRUNCATED read
    # (exit 3), sending the operator to raise RESULTSET_SIZE over a graph that
    # was read completely. Refusing here names the real cause, before the
    # production write. (`cross_wired` is the opposite case and IS copied: a
    # point wired to several sessions has ONE key, so refusing it would leave
    # the parity gate failing forever with nothing able to fix it.)
    if anomalies["duplicate_index"]:
        dupes = "; ".join(
            f"{d['session']}_t{d['index']} ({d['id']})"
            for d in anomalies["duplicate_index"][:5])
        raise CopyRefused(
            f"{len(anomalies['duplicate_index'])} turn key(s) are held by more "
            f"than one node, so the record can hold only one of each and the "
            f"rest can never be filed: {dupes}"
            f"{' …' if len(anomalies['duplicate_index']) > 5 else ''}. Resolve the "
            "duplicates before copying")
    stats = {
        "sessions": 0, "turns": 0, "positioned": 0, "empty_sessions": 0,
        "created_at_absent": [],
        # EVERY class, zeros included: a subset cannot distinguish "0" from
        # "not measured", which is how a silent gap reads as a clean result.
        "anomalies": {k: len(v) for k, v in anomalies.items()},
        # With the IDS: a count alone cannot tell an operator which turns are
        # absent from the record before an irreversible delete.
        "anomaly_ids": {k: v[:100] for k, v in anomalies.items() if v},
    }
    # EVERY session, not just the ones with turns: a session with none still has
    # provenance, and a delete step that touches Session nodes would lose it
    # under a copy that only ever saw turn-bearing sessions.
    for sid in sorted(set(sessions) | set(by_session)):
        bucket = by_session.get(sid, {})
        indexes = sorted(bucket)
        props = sessions.get(sid, {})
        # EVERY spelling that names the column: `build_session_event` drops them
        # all, and a report that named a session as timestamp-absent while the
        # payload carried a real `CreatedAt` is a false entry in the list an
        # operator reads before an irreversible delete. Resolved through the
        # record's own `snake()` rule for the same reason, so the report and the
        # payload cannot disagree about what counts as a timestamp.
        if (sid in sessions
                and all(props.get(k) is None
                        for k in _column_keys(props, "created_at"))):
            stats["created_at_absent"].append(sid)
        events = [build_session_event(
            sid, props, turn_count=len(indexes),
            max_index=indexes[-1] if indexes else -1, event_id=event_id(),
            floor=(record_floor or {}).get(sid))]
        events += [build_turn_event(sid, bucket[i], event_id=event_id())
                   for i in indexes]
        try:
            positioned = emit(sid, events)
        except Exception as exc:
            # The artifact must still say what LANDED. A refusal at session 500
            # of 600 leaves 499 copied, and "re-run to find out how far it got"
            # is the one remedy this tool must not require — a re-run re-appends
            # the journal (see the retry bound in the module docstring).
            #
            # EVERY failure from the emit carries the partial stats, not just
            # `CopyRefused`: the seam raises its own classes
            # (`RecordWriteError`/`RecordConnectError` — an unreachable database,
            # a refused write, a rollback), and a connection dying at session N
            # of M used to escape as an uncaught traceback (exit 1, "the tool
            # cannot start") with NO artifact, on the most likely production
            # failure of all. Nothing is swallowed here: the exception is
            # re-raised untouched, with the context attached.
            if getattr(exc, "stats", None) is None:
                with contextlib.suppress(AttributeError):
                    exc.stats = dict(stats)
            raise
        if positioned is not None:
            stats["positioned"] += 1
        stats["sessions"] += 1
        stats["turns"] += len(indexes)
        if not indexes:
            stats["empty_sessions"] += 1
    return stats


def _turn_keys(events: list[dict]) -> list[str]:
    """The record's ``node_key`` for every turn event in one session's batch.

    The envelope ``id`` first and the snapshot second — the same two carriers the
    record's own ``key_from="id"`` fallback reads (`_key_value`), so this cannot
    name a different row than the insert did. No usable id is a REFUSAL: a turn
    whose key cannot be derived is a row whose tier cannot be proven to have
    landed, and filing it at the default ``'graph'`` is the defect this states.
    """
    keys: list[str] = []
    for event in events:
        if event.get("type") != "PointAdded":
            continue
        key = event.get("id") or (event.get("point") or {}).get("id")
        if not isinstance(key, str) or not key:
            raise CopyRefused(
                "a PointAdded event carries no usable id, so the `record_only` "
                "tier cannot be proven to reach the row it files")
        keys.append(key)
    return keys


def _stamp_record_only_tier(conn: object, org_id: str, graph_id: str,
                            keys: list[str], session_id: str) -> int:
    """State `cache_tier = 'record_only'` on the turn rows just written.

    Called as `_commit_or_refuse`'s ``before_commit`` hook, so the connection is
    the one the seam OPENED, the statement is executed on it BEFORE the commit,
    and a refusal here leaves the session's rows uncommitted (and that call's
    ``finally`` closes the handle).

    A SHORT ROWCOUNT REFUSES. Every key named here was just upserted under the
    same `(org_id, graph_id)` in the same transaction, so the only ways the count
    can differ are the ones that must not be certified: a tenant the policies
    filtered (`app.current_org_id` not this org — the update reaches another
    tenant's rows or none), a row the insert did not land, or a key list that
    stopped naming the rows. Each of those leaves a turn at the DEFAULT
    `'graph'`, which `read_record_view` then hands to `record_plan` — the P1
    outcome — so the copy refuses instead of reporting a clean run.
    """
    if not keys:
        return 0
    cursor = conn.cursor()
    try:
        cursor.execute(_RECORD_ONLY_TIER_SQL, (org_id, graph_id, list(keys)))
        stamped = cursor.rowcount
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
    if stamped != len(keys):
        raise CopyRefused(
            f"{session_id}: the `record_only` tier reached {stamped} of "
            f"{len(keys)} turn row(s) — a row left at the schema's default "
            "'graph' is re-materialised by `rebuild_from_record`, so the copy "
            "would certify a placement the delete phase makes untrue. Nothing "
            "is copied for this session (check that --org-id/--graph-id name "
            "the tenant the record rows are actually under)")
    return stamped


def _journal_emit(journal_path: str, dsn: str, org_id: str, graph_id: str):
    """The production ``emit``: journal position, then the record, committed.

    The position comes from the journal append and the record's rows are
    numbered by it — a record row with no position is the drift #9190 closed.
    The record seam does NOT commit, so this does, and it REFUSES when nothing
    landed rather than returning a position for a write that was rolled back.
    """
    from tortoise.log import EventLog
    from tortoise.record_writer import RecordWriteOutcome, RecordWriter, write_records

    # The journal must already EXIST: `EventLog` on a mistyped or fresh path
    # ordinals from 1, and `journal_event` absorbs the collision with
    # `ON CONFLICT DO NOTHING` — so every position row would be silently
    # discarded while the record came to believe the real journal's seqs were
    # consumed. Fail closed instead of guessing which file is the graph's.
    journal = Path(journal_path)
    if not journal.is_file() or journal.stat().st_size == 0:
        raise CopyRefused(
            f"{journal_path}: not an existing, non-empty journal — positions are "
            "per (org_id, graph_id), and a fresh file would collide with live "
            "history and be swallowed by ON CONFLICT")

    log = EventLog(journal_path)
    # The DSN is passed EXPLICITLY. It used to be accepted and dropped, so a
    # non-default `--dsn-env` wrote into whatever `TORTOISE_RECORD_DSN` named —
    # a different database, exit 0.
    writer = RecordWriter(dsn=dsn)

    def emit(session_id: str, events: list[dict]):
        try:
            positions = [log.append(event) for event in events]
        except Exception as exc:
            # The append is a WRITE, and `EventLog`'s own failure classes are the
            # filesystem's — `_write_record` raises `OSError` on a short write,
            # and `FileIO` raises on ENOSPC/EIO/ESTALE/EACCES (the volumes this
            # path is hardened for). None of those is a class `main` catches, so
            # an uncaught one is a traceback and exit 1 ("the tool cannot
            # start") with NO artifact, on a run that already committed N-1
            # sessions. Convert to this module's refusal: the events may or may
            # not have been appended, which is the same retry caveat the
            # unproven-position message carries.
            raise CopyRefused(
                f"{session_id}: the journal append failed ({type(exc).__name__}) "
                "— no position can be proven, so nothing may be filed; note that "
                f"some of the {len(events)} event(s) may already be appended and "
                "a retry appends them again") from exc
        first = positions[0] if positions else None
        if first is None:
            # ⛔ REFUSE, never write. `position=False` does NOT leave the row
            # unwritten — it emits every LAYER statement and omits only the
            # `journal_event` position row (measured: 6 statements with
            # position=False vs 8 with True, for one session + one turn). Writing
            # there would file turns whose seq nothing proves, which is the
            # #9190 drift this phase exists to close, and the run would still
            # exit 0. Reachable in production: one lock-unhealthy window stamps
            # the journal's path UNHEALTHY and every append in it returns `None`.
            raise CopyRefused(
                f"{session_id}: the journal could not prove a position for these "
                "events — nothing may be filed unnumbered (#9190); note that the "
                f"{len(events)} event(s) ARE now appended and a retry appends "
                "them again")
        if None in positions:
            # A None mid-segment is an UNPROVEN position (#9190), not an
            # interleaved writer — the remedy is different, so the message is.
            raise CopyRefused(
                f"{session_id}: the journal could not prove a position for every "
                "event; filing the rest would number them against someone "
                "else's records")
        if positions != list(range(first, first + len(positions))):
            # `write_records` numbers ONE CONTIGUOUS segment, and the appends
            # take the journal lock one at a time — a concurrent live writer
            # would interleave, and `journal_event`'s ON CONFLICT would keep the
            # peer's event under our point rows.
            raise CopyRefused(
                f"{session_id}: journal positions {positions[:3]}… are not "
                "contiguous — a live writer interleaved with the copy; re-run "
                "with the writer stopped")
        write = write_records(
            events, org_id=org_id, graph_id=graph_id,
            position=True, first_seq=first,
            conn=None, writer=writer)
        # ⛔ THE TIER, IN THE SAME TRANSACTION (see `_RECORD_ONLY_TIER_SQL`), as
        # this call's `before_commit` hook: the statement runs on the connection
        # the seam opened and has NOT committed, so a stamp that cannot reach
        # every turn row leaves the session's rows uncommitted — never a
        # committed copy the cache would re-materialise.
        #
        # The count is captured HERE and read only after the commit returned, so
        # it measures COMMITTED rows and not statements issued. A write that is
        # not APPLIED never runs the hook; `_commit_or_refuse` refuses it with
        # its own, more precise message.
        stamped_rows: list[int] = []
        write = _commit_or_refuse(
            write, RecordWriteOutcome, session_id,
            before_commit=lambda connection: stamped_rows.append(
                _stamp_record_only_tier(connection, org_id, graph_id,
                                        _turn_keys(events), session_id)))
        emit.record_only_turns += stamped_rows[0] if stamped_rows else 0
        return first

    #: Committed turn rows carrying the `record_only` tier. Read by `main` for the
    #: artifact; `0` here is "nothing committed yet", never "no tier stated".
    emit.record_only_turns = 0
    return emit


def _commit_or_refuse(write, applied, session_id: str, *,
                      owns_connection: bool = True, before_commit=None):
    """Commit the seam's connection, and refuse a write that did not land.

    ``owns_connection`` is True because this module calls the seam with
    ``conn=None``, so the seam OPENS the transaction and hands it back
    (`RecordWrite.connection`) for the caller to commit — this function then
    commits it and closes it. A caller that injects ``conn=`` owns both the
    transaction AND the handle: it must pass False, and this function then
    neither accuses it of committing nothing, nor commits, nor closes what it
    does not own.

    ``before_commit(connection)`` runs AFTER the write is proven to have executed
    statements on a connection, and BEFORE the commit — for statements that must
    ride THIS transaction (the copy's `cache_tier` stamp, #10017 R2). Putting it
    here rather than beside the call site is what makes the ordering structural
    and the cleanup single: a hook that raises leaves the transaction uncommitted,
    and the ``finally`` below still closes the handle.
    """
    connection = getattr(write, "connection", None)
    try:
        if getattr(write, "outcome", None) is not applied.APPLIED:
            raise CopyRefused(
                f"{session_id}: the record is not armed / refused the write "
                f"(outcome={getattr(write, 'outcome', None)}) — nothing was copied")
        statements = getattr(write, "statements", 0)
        if not statements:
            raise CopyRefused(
                f"{session_id}: the record accepted the call but wrote 0 "
                "statements — nothing was copied")
        if owns_connection and connection is None:
            # The shape that produced this module's first defect: statements
            # executed on a transaction nobody commits reads as success and
            # writes nothing ("10 statements executed, 0 commits, exit 0").
            raise CopyRefused(
                f"{session_id}: the record executed {statements} statement(s) "
                "but returned no connection to commit — an uncommitted write is "
                "not a copy")
        if before_commit is not None:
            # INSIDE this transaction: its statements commit or vanish with the
            # write's own (the copy's `cache_tier` stamp, #10017 R2).
            #
            # A HOOK THAT THROWS IS A REFUSAL, never an escaping traceback. This
            # hook issues a statement of its own, so it inherits the commit's own
            # failure modes — `UndefinedColumn` on a target where the tier
            # migration has not been applied, `InsufficientPrivilege` on a role
            # without UPDATE on `public.point`, a lost connection — and NONE of
            # those is `RecordWriteError`, so an unconverted one would leave
            # `main`'s three-class handler (`census.CensusError`, `CopyRefused`,
            # `RecordWriteError`) with a traceback and exit 1 ("the tool cannot
            # START") and NO artifact, on a run that already committed N-1
            # sessions. Same conversion, same reasoning, as the commit below.
            try:
                before_commit(connection)
            except CopyRefused:
                raise
            except Exception as exc:
                raise CopyRefused(
                    f"{session_id}: a statement the copy must commit with this "
                    f"write failed after {statements} statement(s) "
                    f"({type(exc).__name__}) — nothing may be counted as "
                    "copied") from exc
        if owns_connection and connection is not None:
            try:
                connection.commit()
            except Exception as exc:
                # The commit is a ROUND TRIP, and a dropped connection is at
                # least as likely to surface here as on `execute`. The driver's
                # failures are the DB-API class tree (`psycopg2.Error` →
                # `OperationalError`/`InterfaceError`), and NONE of them is
                # `RecordWriteError` — so an uncaught one here is a traceback and
                # exit 1 ("the tool cannot start") with NO artifact, on a run
                # that already committed N-1 sessions. A commit that throws is a
                # refusal: nothing about this write may be counted as copied.
                raise CopyRefused(
                    f"{session_id}: the record commit failed after {statements} "
                    f"statement(s) ({type(exc).__name__}) — nothing may be "
                    "counted as copied") from exc
    finally:
        if owns_connection and connection is not None:
            # close must never mask a write failure that is already propagating.
            # Only OUR connection is closed: a caller that injected `conn=` keeps
            # its handle, which is what `owns_connection=False` means.
            with contextlib.suppress(Exception):
                connection.close()
    return write


def _write_artifact(path: str | None, payload: dict) -> None:
    """Write the ``--out`` artifact, never as a crash and never over a verdict.

    ``Path.write_text`` raises the filesystem's own classes (ENOSPC, EACCES,
    ``IsADirectoryError``/``FileNotFoundError`` for a bad path), none of which
    `main` catches — so an unguarded write on the SUCCESS path escaped as a
    traceback and exit 1 ("the tool cannot start") after every session had
    already been committed, and on the REFUSAL path it suppressed the refusal's
    own stderr line. The artifact is duplicated on stdout, so a failed write is
    recoverable; it is loud, and the exit code the run already earned stands.
    """
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True))
    except OSError as exc:
        print(f"copy: the artifact was not written to {path} ({exc}) — the JSON "
              "below is the same report", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = _UsageParser(
        description="#10017 — copy the legacy graph turns into the record.")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--uri-env", default="FALKORDB_CLOUD_URI")
    ap.add_argument("--org-id", required=True,
                    help="the RECORD tenant's org — never inferred from a name")
    ap.add_argument("--graph-id", required=True,
                    help="the RECORD tenant's graph id — never inferred")
    ap.add_argument("--journal", required=False,
                    help="the GRAPH'S OWN journal (its seq space is per "
                         "(org_id, graph_id); a fresh file would collide with "
                         "live history and leave the copy out of rebuild). "
                         "Required unless --dry-run, which opens no journal")
    ap.add_argument("--dsn-env", default="TORTOISE_RECORD_DSN")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report, write nothing")
    ap.add_argument("--out", help="write the summary JSON here")
    args = ap.parse_args(argv)

    # ⛔ The SHAPE of both env flags is checked BEFORE either is read, exactly as
    # the census checks its own (#10031): ``--uri-env``/``--dsn-env`` are the
    # natural place to paste a URI or a DSN by mistake, and the "not set"
    # message below would otherwise echo the value verbatim — a credential in the
    # console or a CI log. Rejected by shape; the VALUE is never echoed.
    for flag, value in (("--uri-env", args.uri_env), ("--dsn-env", args.dsn_env)):
        if not census._URI_ENV_NAME_RE.fullmatch(value):
            raise SystemExit(
                f"copy: {flag} takes the NAME of an environment variable "
                "([A-Za-z_][A-Za-z0-9_]*), not the value itself. Refusing to "
                "echo what was passed (a pasted URI/DSN carries a credential) "
                "— export it and pass the variable name.")

    uri = os.environ.get(args.uri_env)
    if not uri:
        raise SystemExit(f"${args.uri_env} is not set (the graph to read)")
    dsn = os.environ.get(args.dsn_env)
    if not dsn and not args.dry_run:
        raise SystemExit(f"${args.dsn_env} is not set — refuse to copy into "
                         "nothing (use --dry-run to measure only)")
    if args.journal is None and not args.dry_run:
        raise SystemExit("--journal is required: the copy numbers its rows by "
                         "the GRAPH'S OWN journal append, so a run without one "
                         "files no positions (use --dry-run to measure only)")

    # `tortoise.ids.ulid` is the repo's ONE mint — `ulid` is not a dependency,
    # so `import ulid` made every non-dry-run invocation die before connecting.
    from tortoise.ids import ulid

    db, shown = census._connect(uri)
    # ⛔ NOT a bare `census._assert_graph_exists(db, args.graph)`: its refusal is
    # authored for the census — it is prefixed `census:` and ends by offering
    # `--create-if-missing`, a flag THIS tool does not define (measured:
    # `error: unrecognized arguments`, exit 1). The one remedy named in an
    # operator-facing refusal has to be one the operator can actually run, and the
    # refusal has to name the tool that refused. `GRAPH.LIST` is side-effect free,
    # so the check is reused rather than restated; only the message is the copy's.
    try:
        census._assert_graph_exists(db, args.graph)
    except SystemExit:
        names = list(db.list_graphs() or [])
        raise SystemExit(
            f"copy: graph {args.graph!r} does not exist on this instance "
            f"({len(names)} graph(s) there) — and reading a non-existent graph "
            "CREATES it (FalkorDB materialises the keyspace on the first query), "
            "so this tool will not be the thing that creates it. Check --graph."
        ) from None
    graph = db.select_graph(args.graph)

    # The record seam's OWN failure classes are part of the artifact contract:
    # a connection that dies at session N of M is a REFUSAL (exit 2, artifact
    # written reporting what landed), not "the tool cannot start" (exit 1).
    # `RecordConnectError` subclasses `RecordWriteError`, so one name covers both.
    from tortoise.record_writer import RecordWriteError

    emit = None
    try:
        # A dry run proves NO position: it never opens the journal. Returning `0`
        # here made every session count as positioned, so the one number an
        # operator needs before a 38k-row write was the one the dry run
        # fabricated.
        #
        # Built INSIDE this handler on purpose: `_journal_emit` validates the
        # journal as its FIRST action, and a mistyped/fresh `--journal` is the
        # operator's most likely mistake — the one refusal that reaches them
        # before anything is copied, and therefore the one that must read as a
        # verdict (exit 2 with the reason) instead of as a traceback (exit 1).
        emit = (lambda sid, events: None) if args.dry_run else _journal_emit(
            args.journal, dsn, args.org_id, args.graph_id)
        # The record's OWN counts, before anything is emitted: the upsert is
        # absolute, so a copy that does not know the floor can only be trusted
        # not to lower it when the graph happens to hold the larger number.
        # Skipped on a dry run, which opens no record connection at all (and so
        # reports the graph-side numbers the copy WOULD write, not the clamped
        # ones); it refuses on a real run rather than copying blind.
        floor = (None if args.dry_run
                 else _record_floor(dsn, args.org_id, args.graph_id))
        stats = copy_phase(graph, emit, event_id=ulid, record_floor=floor)
    except (census.CensusError, CopyRefused, RecordWriteError) as exc:
        # A refusal is a VERDICT, not a crash: exit 2 with the reason, so the
        # operator (or a CI chain) reads what happened instead of a traceback —
        # the code the census uses for the same class (exit 2, no artifact).
        partial = getattr(exc, "stats", None)
        if partial is not None:
            # ⛔ The SAME three facts the success path names, under the same keys:
            # `graph` is the graph READ, `instance` is the `host:port` it was read
            # from. A refusal is the artifact an operator reads while deciding
            # which graph to re-run against, so filing the endpoint under `graph`
            # here (as this branch did while the success path was fixed) would
            # have one tool meaning two different things by one key — and would
            # leave `--graph` named nowhere in the very artifact that asks the
            # operator to choose a graph.
            partial.update({"graph": args.graph, "instance": shown,
                            "dry_run": args.dry_run,
                            "record_tenant": {"org_id": args.org_id,
                                              "graph_id": args.graph_id},
                            # Committed before the refusal, so the partial
                            # artifact states the tier that DID land.
                            "record_only_turns": getattr(
                                emit, "record_only_turns", None),
                            "refused": str(exc)})
            if args.out:
                _write_artifact(args.out, partial)
            print(json.dumps(partial, indent=2, sort_keys=True))
        print(f"copy: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.dry_run:
        # Not measured, and said so — never 0, which reads as "measured: none".
        stats["positions_measured"] = None
        stats.pop("positioned", None)
    # ⛔ The GRAPH READ, by name — not the instance it was read from. `shown` is
    # the `host:port` endpoint `_connect` returns, and filing it here put two
    # different graphs on one instance behind a byte-identical `graph` field in
    # the artifact an operator reads before an IRREVERSIBLE delete — and made
    # this artifact disagree with the census's, which writes `"graph":
    # graph_name`. The endpoint is still recorded, under its own key.
    stats["graph"] = args.graph
    stats["instance"] = shown
    stats["dry_run"] = args.dry_run
    # The RECORD tenant this run wrote under, in its own artifact: `--graph`,
    # `--org-id` and `--graph-id` are three independent arguments, and the
    # evidence should name the pair that was used rather than leave it to be
    # re-derived from the command line.
    stats["record_tenant"] = {"org_id": args.org_id, "graph_id": args.graph_id}
    # The tier the rows above carry, COUNTED from the transactions that committed
    # it — `_stamp_record_only_tier` refuses unless every turn row of a session
    # was reached, and `emit` adds to this only after its commit returned. `null`
    # is a dry run: it opens no record connection, so no tier was stated (never
    # `0`, which reads as "measured: none" — the same rule as `positions_measured`).
    stats["record_only_turns"] = getattr(emit, "record_only_turns", None)
    if args.out:
        _write_artifact(args.out, stats)
    print(json.dumps(stats, indent=2, sort_keys=True))
    blocking = {k: n for k, n in stats["anomalies"].items()
                if n and k in _BLOCKING_ANOMALIES}
    if blocking:
        # The artifact is written (it reports what DID land), and the exit code
        # carries the verdict — the same contract both sibling gates keep, so an
        # operator chaining `copy && parity-gate` is not told "the backfill
        # completed" by a run that left nodes the record can never hold.
        print(
            f"copy: {sum(blocking.values())} node(s) the record can never hold "
            f"({', '.join(f'{k}={n}' for k, n in sorted(blocking.items()))}) — the "
            "artifact was written; resolve them before the delete",
            file=sys.stderr)
        raise SystemExit(2)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
