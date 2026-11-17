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
  a TORN TAIL is the reader's business, not this tool's).
  ⛔ NOT SEGMENTS. ``EventLog`` reads ONE file; a replay of a segmented layout
  enumerates every present segment (``journal_segments.read_all_segments``). A
  single-file count would therefore seed BELOW the graph's real enumeration, and
  the seed's own guard (``row_max <= floor``) cannot see the difference — it
  would pass and arm a counter that is behind the journal, after which every
  armed write diverges and refuses (#12894: a divergence does not self-heal). So
  a path inside a segmented layout is REFUSED by name, and the operator states
  the true total with ``--enumerated-floor N`` — the tool's other documented
  input, and the only one that can be right for a layout this file cannot count;
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

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first, and the
# resulting `ImportError` reads as "this tool is broken" rather than "your
# interpreter is too old".
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #4053: this is an ENVIRONMENT failure, so it exits EXIT_USAGE (2), not 1 —
    # 1 is REFUSED by the allocator, which means "the graph is unchanged and
    # working, read the reason". A tool that never ran must not be read as an
    # allocator refusal. The constant is defined below; this guard necessarily
    # runs before it (and before the imports, #5128), so the value is spelled out
    # here rather than referenced.
    print(
        f"record-allocator: requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/record_allocator_cli.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)  # EXIT_USAGE — not 1 (REFUSED by the allocator)

import argparse
import json
import os
from pathlib import Path
from typing import Any

#: The arming variable the seam itself reads — imported, never copied (#13043
#: review P2-2): a second spelling of one name is how an operator seeds one
#: record and arms another. Imported LAZILY inside :func:`_record_env_contract`
#: because this module must run on a box with no `psycopg2` and no tortoise
#: import cost until a verb actually needs the record.
def _record_env_contract() -> tuple[str, str]:
    """``(dsn_env, allocate_env)`` — the seam's own names, one copy each."""
    from tortoise.record_writer import RECORD_ALLOCATE_ENV, RECORD_DSN_ENV

    return RECORD_DSN_ENV, RECORD_ALLOCATE_ENV


#: Printed by ``arm`` when it cannot proceed. Kept as a module constant so a
#: test can assert the CONTENT an operator is handed, rather than a docstring.
CUTOVER_ACK_TEXT = """record_allocator_cli: `arm` changes a GLOBAL production switch for ONE graph.

arming a graph makes its live door allocate the journal ordinal. FOUR things
must be true before the flag is rolled, and none of them is this command's to do:

  1. THE FLAG LIVES IN THE WRITER'S ENVIRONMENT, NOT THIS SHELL'S.
     Set %(allocate_env)s=1 for every process that writes records.
     ⛔ Roll it AFTER every graph is armed, and NEVER clear it to revert: on a
     cut-over graph the enumerated arm is REFUSED by name
     (`_refuse_an_enumerated_replay`), so an unset flag turns the record path
     OFF rather than back to the old behaviour. The way back is
     `halt` -> `seed --journal <path>` -> `reopen` -> `arm`.

  2. EVERY GRAPH WRITTEN AFTER THE CUTOVER NEEDS ITS OWN COUNTER ROW, and the
     creation hook that would give it one (`provision_graph_record`, the plan's
     §A7.8 item 8) is NOT IN THIS TREE. A graph created after this point is
     born UNCUT and its records are refused (`cursor_not_active`) until it is
     seeded and armed. Find them with `%(prog)s sweep --org-id <org> --dsn <dsn>`
     and seed each one BEFORE the flag is rolled.

  3. CUTOVER IS PER GRAPH. This command arms ONE graph; every other graph in
     the org stays uncut and is refused while the flag is on.

  4. THE SESSION BACKFILL (`tools/backfill_copy_turns.py`) must have the flag
     set too — it calls the write seam directly and passes `allocate` from the
     same variable. It pre-flights this and refuses before appending.

Re-run with `--acknowledge-cutover` once those are true. Nothing has been armed.
"""

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
    from tortoise import journal_segments
    from tortoise.log import EventLog

    log_path = Path(path)
    if not log_path.exists():
        raise FileNotFoundError(
            f"journal {log_path} does not exist — a graph with no journal has "
            f"floor 0; pass --enumerated-floor 0 explicitly if that is the "
            f"intent, so a mistyped path cannot seed a live graph to 0")
    # ⛔ A SEGMENTED JOURNAL IS NOT ONE FILE — REFUSE, never half-count (#12894
    # review). `EventLog(path)` reads `path` ONLY, while a replay of a segmented
    # layout enumerates EVERY present segment. Counting one segment returns a
    # floor BELOW the graph's real enumeration, and the seed's guard
    # (`row_max <= floor`) cannot tell the difference: it would pass, arm a
    # counter that is behind the journal, and the next file-local append would
    # AGREE with the allocation and be WRITTEN — while a `rebuild_all` over the
    # same directory numbers those records differently. That is a silent split of
    # the two coordinates the whole invariant exists to keep honest, so it must
    # not be reachable.
    #
    # ⛔ THE ANCHOR IS THE LAYOUT, NOT THE MANIFEST FILE. An earlier cut tested
    # only `manifest.exists()`, and that is wrong in the DANGEROUS direction: a
    # sealed segment whose manifest is missing (`seal_active` renames the active
    # file, then writes the manifest — a process killed between the two leaves
    # exactly this) still IS a segmented layout, and `_journal_floor` would have
    # counted one file and under-seeded. `journal_segments` already detects that
    # state and refuses it, so the same facts are tested here: ANY sealed sibling
    # (`SEALED_RE`), or a manifest that exists.
    #
    # A manifest that exists but cannot be parsed therefore refuses too, and that
    # is DELIBERATE rather than collateral: `journal_segments.read_manifest`
    # raises on it for the same reason — an unreadable manifest is not an
    # "absent" one, and treating it as absent is how a recorded boundary gets
    # laundered into a full-reconstruction claim. On such a directory the true
    # enumeration is unknown to this tool, which is precisely what
    # `--enumerated-floor N` is for.
    manifest = log_path.parent / journal_segments.MANIFEST_NAME
    sealed = sorted(p.name for p in log_path.parent.iterdir()
                    if journal_segments.SEALED_RE.match(p.name))
    if sealed or manifest.exists():
        found = (f"sealed segment(s) {sealed}" if sealed
                 else journal_segments.MANIFEST_NAME)
        raise ValueError(
            f"journal {log_path} belongs to a SEGMENTED layout ({found} in "
            f"{log_path.parent}), and this tool counts ONE FILE. A replay of "
            f"that layout enumerates every present segment, so a single-file "
            f"count would seed BELOW it — and the seed's guard cannot see the "
            f"difference. Pass the layout's true total with --enumerated-floor "
            f"N (measure it with `journal_segments.read_all_segments(<graph "
            f"dir>, salvage=True)`, which includes the active tail), or point "
            f"--journal at a graph that has never sealed")
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
    _dsn_env, allocate_env = _record_env_contract()
    payload = {
        "org_id": args.org_id,
        "graph_id": args.graph_id,
        "cut_over": cursor is not None,
        "seq": None if cursor is None else cursor[0],
        "mode": None if cursor is None else cursor[1],
        "journal_row_max": row_max,
        "allocates": cursor is not None and cursor[1] == ra.MODE_ACTIVE,
        # ⛔ `allocates` IS THE **RECORD'S** STATE, NOT THIS BOX'S (#13043 review
        # P2-4). It said `true` on a graph whose live door is unarmed, which an
        # operator reads as "the door is allocating". The door's switch is an
        # ENVIRONMENT VARIABLE of the WRITER, which this process cannot see — so
        # the reading of its own environment is reported as exactly that, and
        # `None` means "unset HERE", never "off in production".
        "allocates_scope": "record",
        "record_allocate_env": allocate_env,
        "record_allocate_seen_here": os.environ.get(allocate_env),
        "metrics": ra.metrics(),
    }
    if args.floor is not None:
        # The count the seed would use, beside the count the record holds. A
        # `journal_row_max` ABOVE this is the `divergence` refusal; an EQUAL or
        # lower one is seedable, and `row_max < floor` is the `None`-position tail.
        #
        # ⛔ THIS IS THE **SEED'S** REFUSAL CONDITION, and it is deliberately not
        # the only divergence reported. `write_live_record` refuses on a DIFFERENT
        # pair, so a wedged armed graph can show `divergence: false` here while the
        # door refuses every write. `position_divergence` below reports the DOOR's
        # condition; the door's own warning names that field, so the two cannot be
        # mistaken for each other (#12894 review cycle 4, P2).
        payload["journal_enumerated_floor"] = args.floor
        payload["divergence"] = row_max > args.floor
        payload["healed_tail"] = args.floor > row_max
        # ⛔ THE DOOR'S OWN CONDITION, and read the arithmetic before changing it.
        # The door issues `cursor + 1` and compares it with the position the append
        # reported, which on a SINGLE-FILE journal holding `floor` records is
        # `floor + 1`. So the door ACCEPTS iff `cursor + 1 == floor + 1`, i.e. iff
        # `cursor == floor`. The `+ 1` appears on BOTH sides and cancels — which is
        # why the test is `cursor != floor` and not `cursor + 1 != floor`: the
        # latter is off by one and reports the condition INVERTED (true on a
        # healthy armed graph, false on the wedged one it exists to expose), which
        # is worse than no field at all because the door's WARNING names this field
        # as authoritative (#12894 review cycle 5, P1 — measured).
        #
        # ⛔ SCOPE: SINGLE-FILE JOURNALS ONLY, and the limit is a real one rather
        # than a hedge. On a SEGMENTED layout `EventLog.append` reports the
        # FILE-LOCAL index (it restarts at 1 after a seal), while every `floor` this
        # tool can be given is the SEGMENT-SPANNING enumeration — `_journal_floor`
        # now refuses a segmented layout by name, and its message sends the operator
        # to `read_all_segments`, which counts every segment. Those are different
        # coordinates, so the two sides of this comparison are not the door's two
        # sides and the field cannot be trusted there: read it only for a journal
        # that has never sealed, and treat a seal as its own gated transition.
        payload["position_divergence"] = (
            cursor is not None
            and cursor[1] == ra.MODE_ACTIVE
            and cursor[0] != args.floor
        )
    return payload


def _cmd_seed(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    from tortoise import record_allocator as ra

    result = ra.seed(conn, org_id=args.org_id, graph_id=args.graph_id,
                     enumerated_floor=args.floor)
    conn.commit()
    return _result_row(result)


def _cmd_sweep(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    """READ-ONLY: which of this org's graphs are BORN UNCUT (#13043 P1-1).

    WHY A VERB AND NOT A SENTENCE. `arm` can only refuse; the state it must warn
    about is PER GRAPH and grows over time (every graph created after the
    cutover), so an operator needs a way to FIND them. This is that way, and it
    is the plan's own named mechanism for the graph the best-effort mint creates
    (`§A7.8 item 8`, the row reached *"through the reconciliation sweep"*).

    ⛔ WHAT IT CANNOT SEE, stated so the output is not read as coverage: a graph
    that has never been written leaves no row in `journal_event`, and the record
    role cannot read `public.graphs` (no grant — see :data:`ra.UNCUT_GRAPHS_SQL`).
    Such a graph has lost nothing yet; it becomes visible — and actionable — at
    its first append.

    ⛔ THE FLOOR IS NOT `max_seq`. For a born-uncut graph the journal's own
    COUNT is the floor, and a `max_seq` that is LOWER than it (lines whose rows
    were refused) seeds the counter BEHIND the journal, which does not heal: it
    wedges every later armed write. So each entry carries `journal_max_seq`,
    named for the aggregate it actually ran (`MAX(seq)`), and this verb never
    claims that number is the count: the count is reported only where an
    enumeration really exists — an operator's `--enumerated-floor` or a
    `--journal` this tool counted itself — and `floor_is_the_journals_count`
    answers `None` (UNKNOWN, with `floor_source` saying why) everywhere else
    (#13043 review findings B and 4).
    """
    from tortoise import record_allocator as ra

    # ⛔ NO ROLLBACK BETWEEN THE SCOPE AND THE PER-GRAPH READ (#13043 review
    # finding B, MEASURED). `ra.scope_tenant` sets `set_config('app.current_org_id',
    # %s, true)` — TRANSACTION-LOCAL to PostgreSQL — and `journal_event_app_tenant`
    # filters on `current_setting('app.current_org_id', true)`. A `conn.rollback()`
    # here ENDS that transaction, the GUC goes with it, and every per-graph
    # `JOURNAL_MAX_SQL` then answers `COALESCE(MAX(seq), 0)` = **0** under a NULL
    # predicate: a pre-flight that fabricates the one datum it exists to report.
    # The single `conn.rollback()` at the end of this function is the read-only
    # cleanup, AFTER every scoped read.
    ra.scope_tenant(conn, args.org_id)
    with conn.cursor() as cur:
        cur.execute(ra.UNCUT_GRAPHS_SQL, (args.org_id,))
        graphs = [row[0] for row in cur.fetchall()]
    if args.floor is not None and len(graphs) != 1:
        # ⛔ THE FLOOR IS PER GRAPH, AND THIS VERB USED TO HAND IT TO ALL OF THEM
        # (#13043 review finding 4). The comment below already claimed it was
        # never applied to more than one graph; now the claim is enforced.
        conn.rollback()
        raise ValueError(
            f"sweep found {len(graphs)} born-uncut graph(s) for {args.org_id}, "
            "so a single --enumerated-floor/--journal cannot be attributed to "
            "one of them — the floor is PER GRAPH. Re-run `sweep` without it, "
            "then `seed` each graph with ITS OWN journal")
    entries: list[dict[str, Any]] = []
    for graph_id in graphs:
        with conn.cursor() as cur:
            cur.execute(ra.JOURNAL_MAX_SQL, (args.org_id, graph_id))
            row = cur.fetchone()
        # ⛔ `journal_max_seq` IS `MAX(seq)` AND NOTHING ELSE. It had been called
        # `journal_row_max`, beside a `floor_is_the_journals_count: True` literal,
        # which asserts the two are the same quantity — false whenever the seq
        # axis has a gap or a tail, which `_cmd_status` treats as a real state in
        # both directions (#13043 review findings B and 4).
        entry: dict[str, Any] = {
            "graph_id": graph_id,
            "journal_max_seq": int(row[0]) if row else 0,
            "seed_command": (
                f"record_allocator_cli.py seed --org-id {args.org_id} "
                f"--graph-id {graph_id} --journal <that graph's journal path>"),
        }
        if args.floor is not None:
            # Reachable only for a SINGLE graph (the refusal above), and it is a
            # COUNT: either the journal's own enumeration (`--journal`, counted
            # by `_journal_floor`) or the operator's stated count.
            entry["enumerated_floor"] = args.floor
            entry["floor_source"] = (
                "journal" if args.journal is not None else "operator")
            entry["floor_is_the_journals_count"] = (
                args.floor == entry["journal_max_seq"])
        else:
            entry["enumerated_floor"] = None
            entry["floor_source"] = None
            entry["floor_is_the_journals_count"] = None
        entries.append(entry)
    conn.rollback()
    payload: dict[str, Any] = {
        "org_id": args.org_id,
        "command": "sweep",
        "uncut_graphs": entries,
        "count": len(entries),
        # ⛔ NOT A LITERAL `True` (#13043 review finding 4): with no enumeration
        # for a graph this verb has not been handed, the honest answer is
        # UNKNOWN. A pre-flight whose whole purpose is that a false CLEAN is the
        # worst answer it can give must not assert the one thing it did not read.
        "floor_is_the_journals_count": (
            None if not entries else entries[0]["floor_is_the_journals_count"]),
        "cannot_see": (
            "a graph that has never been written (no journal_event row) — the "
            "record role has no grant on public.graphs; such a graph has lost "
            "nothing yet"),
    }
    if payload["floor_is_the_journals_count"] is None:
        # The NUMBER beside the flag is `MAX(seq)`, and nothing here counted the
        # journal — so the reason is stated rather than left to be inferred from
        # a `null`.
        payload["floor_is_the_journals_count_reason"] = (
            "unknown: `sweep` reads MAX(seq), not the journal's enumeration — "
            "pass --journal <that graph's path> (or --enumerated-floor N) to "
            "have the count reported beside it")
    return payload


def _cmd_transition(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    from tortoise import record_allocator as ra

    verb = {"arm": ra.arm, "halt": ra.halt, "reopen": ra.reopen}[args.command]
    seq = verb(conn, org_id=args.org_id, graph_id=args.graph_id)
    conn.commit()
    payload: dict[str, Any] = {"org_id": args.org_id, "graph_id": args.graph_id,
                              "command": args.command, "seq": seq}
    if args.command == "arm":
        # ⛔ THE FLAG IS NOT ARMED BY THIS COMMAND, AND THE OPERATOR MUST BE TOLD
        # WHICH ONE IS (#13043 P1-2). `arm` moves the RECORD's counter; the
        # door's arm is `TORTOISE_RECORD_ALLOCATE` in the WRITER's environment.
        # They are two switches on purpose and a reader who confuses them lands
        # on the state that eats rows.
        _dsn_env, allocate_env = _record_env_contract()
        seen = os.environ.get(allocate_env)
        payload["record_allocate_env"] = allocate_env
        payload["record_allocate_seen_here"] = seen
        payload["door_armed_by_this_command"] = False
        payload["next"] = (
            f"{allocate_env}=1 in the WRITER's environment, and every other "
            "graph of this org seeded+armed first (`sweep` lists the ones the "
            "record has seen). NEVER clear it to revert: on a cut-over graph "
            "the enumerated arm is refused, so unsetting it turns the record "
            "path OFF")
    return payload


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="record_allocator_cli",
        description="Seed / arm / halt / reopen the record's per-graph ordinal "
                    "allocator, or read its state (#12894).")
    ap.add_argument("command",
                    choices=("status", "seed", "arm", "halt", "reopen", "sweep"))
    ap.add_argument("--org-id", required=True)
    #: `sweep` is the one verb that is ORG-scoped rather than graph-scoped — it
    #: exists to find graphs nobody has named yet — so `--graph-id` cannot be
    #: required by the parser for it. It IS required for every other verb, and
    #: `main` refuses a missing one by name rather than letting a graph-scoped
    #: verb run with `None`.
    ap.add_argument("--graph-id", default=None)
    ap.add_argument("--dsn", default=None,
                    help="the record's app-role DSN (default: the seam's own "
                         "DSN variable)")
    ap.add_argument("--journal", default=None,
                    help="the graph's journal PATH; the seed's floor is counted "
                         "with the journal's own reader")
    ap.add_argument("--enumerated-floor", type=int, default=None, dest="floor",
                    help="the number of records a replay of this graph's journal "
                         "enumerates, stated by the operator (mutually exclusive "
                         "with --journal)")
    ap.add_argument("--acknowledge-cutover", action="store_true",
                    dest="acknowledge_cutover",
                    help="required by `arm`: confirms the global flag, the "
                         "per-graph scope and the born-uncut graphs have been "
                         "read (see the refusal text)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)

    if args.command != "sweep" and not args.graph_id:
        ap.error(f"{args.command} is graph-scoped — pass --graph-id (only `sweep` "
                 "walks the org)")
    if args.floor is not None and args.journal is not None:
        ap.error("--journal and --enumerated-floor are mutually exclusive: one "
                 "reading of the journal, not two")
    if args.command == "seed" and args.floor is None and args.journal is None:
        ap.error("seed needs the journal's enumeration — pass --journal PATH or "
                 "--enumerated-floor N. It is not defaulted: 0 is a real answer "
                 "for an empty graph, so a default would seed a populated graph "
                 "to 0 and the next live allocation would collide with row 1")

    # ⛔ `arm` REQUIRES AN EXPLICIT ACKNOWLEDGEMENT (#13043 review P1-2), and the
    # refusal is where an operator learns the four preconditions — the flag's
    # HOME, the born-uncut graphs, the per-graph scope, and the backfill. Without
    # it, `seed` + `arm` followed by rolling the flag is a state that EATS rows:
    # the live door is on the enumerated arm while the graph is cut over, so
    # `_refuse_an_enumerated_replay` refuses every record while the journal keeps
    # advancing (`counter - floor` rows lost, WARNed per record, never raised —
    # `write_live_record` is fail-soft by contract), and the remedy is the
    # allocator's operator path, not "set the flag".
    if args.command == "arm" and not args.acknowledge_cutover:
        _dsn_env, allocate_env = _record_env_contract()
        print(CUTOVER_ACK_TEXT % {"allocate_env": allocate_env,
                                  "prog": ap.prog}, file=sys.stderr)
        _emit(args, {"org_id": args.org_id, "graph_id": args.graph_id,
                     "command": "arm", "refused": "unacknowledged-cutover",
                     "requires": "--acknowledge-cutover",
                     "env": allocate_env})
        return EXIT_REFUSED

    if args.journal is not None:
        try:
            args.floor = _journal_floor(args.journal)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"record_allocator_cli: {exc}", file=sys.stderr)
            return EXIT_USAGE

    dsn_env, _allocate_env = _record_env_contract()
    dsn = args.dsn or os.environ.get(dsn_env)
    if not dsn:
        print(f"record_allocator_cli: no DSN — pass --dsn or set {dsn_env}",
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
        elif args.command == "sweep":
            payload = _cmd_sweep(conn, args)
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
