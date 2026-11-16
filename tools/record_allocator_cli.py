#!/usr/bin/env python3
"""The operator door onto the record's ordinal allocator (#12894, V5 item 2).

WHY THIS EXISTS. `record_allocator.seed/arm/halt/reopen` were merged, correct and
**unreachable**: measured on `#12894` (evidence `docs/evidence/12894-landed-verify`
V3), outside `tests/` nothing imported the module except two call sites that use
it for error MAPPING, and `supabase/migrations/20261116000001_…sql` inserts no
row. So every graph was uncut and the cutover was a code path with no operator
door — the plan's `§A7.7` step 6.2, specified and unbuilt.

WHAT THE VERBS ARE FOR, one line each:

* ``status`` — is this graph cut over, to what, and what does the counter say
  against the row table. Read-only. This is the verb to run FIRST, always.
* ``seed``  — write the counter row from the durable facts (the record's max
  position and the journal's own enumeration). **Idempotent and monotone**
  (`GREATEST`), and refused from ``active``.
* ``arm``   — ``seeded → active``. After this the counter issues positions and
  the live door can allocate.
* ``halt``  — ``active → halted``. The counter is KEPT: the carried numbers are
  the only record of what was issued.
* ``reopen``— ``halted → seeded``, so a halted graph can be re-seeded and re-armed.

THE SEED'S ONE INPUT THAT CANNOT BE DERIVED. `enumerated_floor` is *how many
records a replay of THIS graph's journal enumerates* — the caller's fact. Two
ways to give it, and the tool refuses to guess a third:

* ``--journal PATH`` — the journal's OWN reader counts it
  (``EventLog.read_all(salvage=True)``, the same read the replay engines use, so
  segments and torn tails are the reader's business, not this tool's);
* ``--enumerated-floor N`` — stated by an operator who knows, for a journal this
  box cannot read (the volume belongs to another lane, or the graph is hosted).

⛔ NEITHER IS A DEFAULT. ``0`` is a real answer for an empty graph, so a default
would silently seed a graph with 400 records to 0 — after which the first live
allocation collides with row 1 and the door refuses every write. `--enumerated-floor`
and `--journal` are therefore mutually exclusive AND one is required.

⛔ THE DSN MUST BE THE RECORD'S APP ROLE. `record_projection.scope_connection`
refuses a superuser / BYPASSRLS / table-owner connection (that is the #9600
role gate), so a ``postgres``-style DSN fails closed on the first statement with
a message this tool surfaces rather than wraps. It is not a configuration
preference; it is the tenant boundary.

EXIT CODES (the rail's contract, not decoration):

  0  the verb happened (``status`` walked, ``seed``/``arm``/``halt``/``reopen`` wrote)
  1  REFUSED by the allocator, with ``reason`` named on stderr (``divergence``,
     ``armed``, ``missing``) — the graph is unchanged and working; read the
     reason, do not re-run blindly
  2  usage / environment (bad arguments, no DSN, no psycopg2, unreachable record)

Usage:

    uv run python tools/record_allocator_cli.py status \\
        --org-id org-1 --graph-id graph-1
    uv run python tools/record_allocator_cli.py seed \\
        --org-id org-1 --graph-id graph-1 --journal /data/journals/graph-1/events.jsonl
    uv run python tools/record_allocator_cli.py arm --org-id org-1 --graph-id graph-1

The DSN comes from ``--dsn`` or ``TORTOISE_RECORD_DSN`` (the same variable the
write seam arms from). ``--json`` prints one JSON object on stdout and nothing
else, so a caller can parse it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

#: The arming variable the seam itself reads — one name, so an operator cannot
#: seed one record and arm another.
RECORD_DSN_ENV = "TORTOISE_RECORD_DSN"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def _journal_floor(path: str) -> int:
    """How many records a replay of `path` enumerates.

    ⛔ `salvage=True`, deliberately: it is the READ THE REPLAY ENGINES USE
    (`rebuild_all` / `recover_from_log`), so a torn tail or a damaged middle line
    is classified by the journal's own reader and counted the same way a rebuild
    would count it. Counting the file's newlines here instead would be a second
    reader — and the number this function returns becomes a `seq` that live
    writes then key on, so a second reader is a second answer to "how long is the
    journal".
    """
    from tortoise.log import EventLog

    log_path = Path(path)
    if not log_path.exists():
        raise FileNotFoundError(
            f"journal {log_path} does not exist — a graph with no journal has "
            f"floor 0; pass --enumerated-floor 0 explicitly if that is the "
            f"intent, so a mistyped path cannot seed a live graph to 0")
    return len(EventLog(log_path).read_all(salvage=True))


def _connect(dsn: str) -> Any:
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise SystemExit(
            "psycopg2 is not installed — the record's Postgres legs need the "
            "`postgres` extra: uv sync --extra postgres") from exc
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def _result_row(result: Any) -> dict[str, Any]:
    return {
        "org_id": result.org_id,
        "graph_id": result.graph_id,
        "seq": result.seq,
        "mode": result.mode,
        "row_max": result.row_max,
        "enumerated_floor": result.enumerated_floor,
        "healed_tail": result.healed_tail,
        "burned": result.burned,
    }


def _cmd_status(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Read-only: what the counter says, and what the rows say it should be.

    ⛔ IT ANSWERS THE DIVERGENCE REMEDY, which is the reason `--journal` is
    accepted here at all: `write_live_record` tells an operator whose allocated
    number and journal position disagreed to "check the journal's enumeration",
    and a `status` that reported only `journal_row_max` would leave them unable to
    compare it with the journal. With `--journal` the response carries
    `journal_enumerated_floor` too, so the two readings sit side by side in one
    object — which is the whole decision (`row_max` vs the journal's count), and
    the seed's own input.
    """
    from tortoise import record_allocator as ra

    cursor = ra.read_cursor(conn, org_id=args.org_id, graph_id=args.graph_id)
    with conn.cursor() as cur:
        cur.execute(ra.JOURNAL_MAX_SQL, (args.org_id, args.graph_id))
        row = cur.fetchone()
    conn.rollback()
    row_max = int(row[0]) if row else 0
    payload = {
        "org_id": args.org_id,
        "graph_id": args.graph_id,
        "cut_over": cursor is not None,
        "seq": None if cursor is None else cursor[0],
        "mode": None if cursor is None else cursor[1],
        "journal_row_max": row_max,
        "allocates": cursor is not None and cursor[1] == ra.MODE_ACTIVE,
        "metrics": ra.metrics(),
    }
    if args.floor is not None:
        # The count the seed would use, beside the count the record holds. A
        # `journal_row_max` ABOVE this is the `divergence` refusal; an EQUAL or
        # lower one is seedable, and `row_max < floor` is the `None`-position tail.
        payload["journal_enumerated_floor"] = args.floor
        payload["divergence"] = row_max > args.floor
        payload["healed_tail"] = args.floor > row_max
    return payload


def _cmd_seed(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    from tortoise import record_allocator as ra

    result = ra.seed(conn, org_id=args.org_id, graph_id=args.graph_id,
                     enumerated_floor=args.floor)
    conn.commit()
    return _result_row(result)


def _cmd_transition(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    from tortoise import record_allocator as ra

    verb = {"arm": ra.arm, "halt": ra.halt, "reopen": ra.reopen}[args.command]
    seq = verb(conn, org_id=args.org_id, graph_id=args.graph_id)
    conn.commit()
    return {"org_id": args.org_id, "graph_id": args.graph_id,
            "command": args.command, "seq": seq}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="record_allocator_cli",
        description="Seed / arm / halt / reopen the record's per-graph ordinal "
                    "allocator, or read its state (#12894).")
    ap.add_argument("command", choices=("status", "seed", "arm", "halt", "reopen"))
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--graph-id", required=True)
    ap.add_argument("--dsn", default=None,
                    help=f"the record's app-role DSN (default: ${RECORD_DSN_ENV})")
    ap.add_argument("--journal", default=None,
                    help="the graph's journal PATH; the seed's floor is counted "
                         "with the journal's own reader")
    ap.add_argument("--enumerated-floor", type=int, default=None, dest="floor",
                    help="the number of records a replay of this graph's journal "
                         "enumerates, stated by the operator (mutually exclusive "
                         "with --journal)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)

    if args.floor is not None and args.journal is not None:
        ap.error("--journal and --enumerated-floor are mutually exclusive: one "
                 "reading of the journal, not two")
    if args.command == "seed" and args.floor is None and args.journal is None:
        ap.error("seed needs the journal's enumeration — pass --journal PATH or "
                 "--enumerated-floor N. It is not defaulted: 0 is a real answer "
                 "for an empty graph, so a default would seed a populated graph "
                 "to 0 and the next live allocation would collide with row 1")

    if args.journal is not None:
        try:
            args.floor = _journal_floor(args.journal)
        except (FileNotFoundError, OSError) as exc:
            print(f"record_allocator_cli: {exc}", file=sys.stderr)
            return EXIT_USAGE

    dsn = args.dsn or os.environ.get(RECORD_DSN_ENV)
    if not dsn:
        print(f"record_allocator_cli: no DSN — pass --dsn or set {RECORD_DSN_ENV}",
              file=sys.stderr)
        return EXIT_USAGE

    try:
        conn = _connect(dsn)
    except SystemExit as exc:
        print(f"record_allocator_cli: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:
        print(f"record_allocator_cli: the record could not be reached "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_USAGE

    from tortoise import record_allocator as ra

    try:
        if args.command == "status":
            payload = _cmd_status(conn, args)
        elif args.command == "seed":
            payload = _cmd_seed(conn, args)
        else:
            payload = _cmd_transition(conn, args)
    except ra.SeedRefused as exc:
        # ⛔ THE REASON IS THE REPORT. `divergence` / `armed` / `missing` have
        # OPPOSITE remedies, and the graph is UNCHANGED — an operator who re-runs
        # blind turns one refusal into a second one.
        with_conn = {"org_id": args.org_id, "graph_id": args.graph_id,
                     "refused": exc.reason, "row_max": exc.row_max,
                     "enumerated_floor": exc.enumerated_floor,
                     "seq": None, "mode": None}
        _emit(args, with_conn)
        print(f"record_allocator_cli: SEED REFUSED ({exc.reason}): {exc}",
              file=sys.stderr)
        return EXIT_REFUSED
    except ra.RecordAllocatorError as exc:
        _emit(args, {"org_id": args.org_id, "graph_id": args.graph_id,
                     "refused": type(exc).__name__})
        print(f"record_allocator_cli: REFUSED ({type(exc).__name__}): {exc}",
              file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:
        print(f"record_allocator_cli: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_USAGE
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()

    _emit(args, payload)
    return EXIT_OK


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    for key in sorted(payload):
        print(f"{key}: {payload[key]}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
