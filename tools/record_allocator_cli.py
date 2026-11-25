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
* ``sweep`` — READ-ONLY: which of this org's graphs the record has seen writing
  with NO counter row ("born uncut").
* ``backfill`` — ``sweep``'s population, SEEDED (#14582). Idempotent, one
  transaction per graph, and it reports its three counts:
  ``N considered / M seeded / K refused`` with a reason per refusal. Refusal is
  a legal outcome here — a graph whose journal enumeration and record positions
  disagree is NOT silently seeded — and it is never a silent skip.
  ``--dry-run`` exercises the REAL decision path and rolls the whole run back,
  so the operator can see the population and the refusals before writing
  anything.

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

  2. EVERY GRAPH WRITTEN AFTER THE CUTOVER NEEDS ITS OWN COUNTER ROW.
     `#14582` added the creation hook that gives a NEW graph one
     (`record_writer.provision_graph_record`, called at every create door), so
     graphs created from that point on are born cut over. ⛔ IT IS
     BEST-EFFORT: a record outage at creation does not fail the create, it
     leaves the graph UNCUT — which `/health` reports as
     `record.armed_but_uncut`. ⛔ `%(prog)s backfill` does NOT heal that graph
     (#14587 review P1-2): it seeds `sweep`'s population, and `sweep` reads
     `public.journal_event`, so a graph whose record writes are being REFUSED has
     no row there to find — however many times it writes. Seed it PER GRAPH with
     `%(prog)s seed --org-id <org> --graph-id <id> --enumerated-floor 0` (correct
     for a graph that has never written to the record; a graph with existing
     record rows is REFUSED `divergence`, never silently seeded) and then
     `%(prog)s arm --org-id <org> --graph-id <id> --acknowledge-cutover`. The
     hook also does not reach the graphs that already existed when it landed, and
     `backfill` IS the verb for those. So find THAT population with `%(prog)s sweep --org-id <org> --dsn <dsn>`, seed it with
     `%(prog)s backfill --org-id <org> --dry-run` (read it, write nothing) and
     then for real — BEFORE the flag is rolled.

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
    COUNT is the floor, and the two are NOT interchangeable: a refused seq leaves
    a GAP, so `COUNT(*) < MAX(seq)`, and the counter numbers POSITIONS — so a
    floor read off `MAX(seq)` would allocate positions this journal never
    enumerated. (The reverse ordering, `COUNT(*) > MAX(seq)`, is unrepresentable
    and must not be reasoned from: `journal_event` carries `PRIMARY KEY (org_id, graph_id, seq)` +
    `CHECK (seq > 0)`, so N distinct positive seqs with maximum M always satisfy
    N <= M — see :data:`ra.JOURNAL_COUNT_SQL`.) So each entry carries
    `journal_max_seq`,
    named for the aggregate it actually ran (`MAX(seq)`), and this verb never
    claims that number is the count: `journal_count` is the `COUNT(*)` of the
    same rows, and `floor_is_the_journals_count` compares the floor with THAT,
    answering `None` (UNKNOWN, with `floor_source` saying why) wherever no
    enumeration was supplied at all (#13043 review findings B and 4, and the
    round-3 blocking finding that the comparison used `max_seq` under a name
    that says count).
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
        with conn.cursor() as cur:
            # ⛔ THE FLOOR'S OWN QUANTITY (#13043 review, blocking). This used to
            # answer `floor_is_the_journals_count` by comparing the floor with
            # `journal_max_seq` below — a different number under the wrong name,
            # so the field's one job ("is this the journal's count?") was
            # answered about `MAX(seq)` instead. Measured on a dense journal the
            # two coincide, which is why a false CLEAN survived: the divergence
            # only shows up on the state the verb exists to find (a refused seq
            # leaves a GAP — `COUNT(*) < MAX(seq)`; the reverse cannot exist
            # under the journal's `PRIMARY KEY (org_id, graph_id, seq)` +
            # `CHECK (seq > 0)`). Read the count, and report it beside the max so
            # both numbers are in the output rather than one standing in for the
            # other.
            cur.execute(ra.JOURNAL_COUNT_SQL, (args.org_id, graph_id))
            count_row = cur.fetchone()
        # ⛔ `journal_max_seq` IS `MAX(seq)` AND NOTHING ELSE. It had been called
        # `journal_row_max`, beside a `floor_is_the_journals_count: True` literal,
        # which asserts the two are the same quantity — false whenever the seq
        # axis has a gap (`COUNT(*) < MAX(seq)`). The opposite ordering cannot
        # exist, and the only other direction anything here cares about is
        # `_cmd_status`'s `healed_tail`: a STATED floor above the max, where the
        # statement is the operator's `--enumerated-floor` OR a count taken from
        # `--journal` (`_journal_floor`) — a comparison against a stated number
        # either way, never a reading of `MAX(seq)` (#13043 review findings B and
        # 4, plus the re-review rows that caught "or a tail" stated as a state of
        # the seq axis).
        journal_count = int(count_row[0]) if count_row else 0
        entry: dict[str, Any] = {
            "graph_id": graph_id,
            "journal_max_seq": int(row[0]) if row else 0,
            "journal_count": journal_count,
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
            # ⛔ AGAINST `journal_count`, NOT `journal_max_seq`. `seed` writes the
            # floor as the counter's number, and the counter numbers POSITIONS —
            # so the quantity the floor must equal is how many the journal holds.
            entry["floor_is_the_journals_count"] = (
                args.floor == journal_count)
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
            "a graph the record has never seen write (no `journal_event` row) "
            "— the record role has no grant on public.graphs. ⛔ ON AN ARMED "
            "DEPLOYMENT THAT IS THE BORN-UNCUT POPULATION ITSELF, and it does "
            "NOT 'lose nothing yet': its record writes are REFUSED "
            "(`cursor_not_active`), so it accumulates no `journal_event` row "
            "however many times it writes and never appears in this list. "
            "Seed one per graph with `seed --graph-id … --enumerated-floor 0` "
            "then `arm`; `/health`'s `record.armed_but_uncut` names them"),
    }
    if payload["floor_is_the_journals_count"] is None:
        # ⛔ THE REASON MUST NAME THE CAUSE THE `null` ACTUALLY HAD (#13043
        # re-review, P2). This sentence used to read "`sweep` reads MAX(seq), not
        # the journal's enumeration — pass --journal … to have the count
        # reported beside it". The premise is false as written: the verb reads
        # `COUNT(*)` and reports it as `journal_count` beside `journal_max_seq`
        # on EVERY entry, so it neither omits the count nor needs telling to
        # report it. (`--journal` IS a working remedy — `main` counts it into
        # `args.floor` — but what it supplies is the FLOOR to compare against,
        # not the count's reporting, which is the part the sentence got
        # backwards.) The flag is `null` because NO FLOOR was handed in to
        # compare: name that, name what the verb already answered, and name the
        # input that would answer it.
        if not entries:
            # ⛔ AND DO NOT OVER-CLAIM THE ABSENCE (#13043 re-review, P3): this
            # verb reads `public.journal_event` only, so a born-uncut graph that
            # has never been written leaves no row and is invisible — exactly
            # what `cannot_see` below says. "none in this org" would be a read
            # this sweep did not perform.
            payload["floor_is_the_journals_count_reason"] = (
                "unknown: no born-uncut graph the record can see in this org, "
                "so there was no journal whose count could be compared with a "
                "floor (a graph that has never been written leaves no "
                "`journal_event` row — see `cannot_see`)")
        else:
            payload["floor_is_the_journals_count_reason"] = (
                "unknown: no floor was supplied, so nothing was compared with "
                "the journal's count — the count itself IS read and is "
                "reported as `journal_count` beside `journal_max_seq`; pass "
                "--enumerated-floor N (or --journal <that graph's path>) to "
                "have the floor compared")
    return payload


def _cmd_backfill(conn: Any, args: argparse.Namespace) -> dict[str, Any]:
    """SEED `sweep`'s population — the graphs the creation hook did not reach (#14582).

    WHY THIS VERB EXISTS AT ALL. `#14582`'s hook is BEST-EFFORT by design (a
    record outage at creation must not 500 a user's graph), so there are two
    populations it cannot cover: the graphs created BEFORE it landed, and the
    ones whose hook failed — and ⛔ ONLY THE FIRST IS THIS VERB'S (#14587 review
    P1-2). The population below is `public.journal_event` minus `record_cursor`:
    the graphs the RECORD has SEEN writing. On an armed deployment a graph whose
    hook failed writes no `journal_event` row at all (`ALLOCATION_SQL` is scoped
    to `mode='active'`, so the door refuses), so it is ABSENT here for as long as
    it is broken and `considered: 0` is NOT evidence that it is healthy. Those
    graphs need `seed --graph-id … --enumerated-floor 0` + `arm` per graph, which
    `/health`'s `record.armed_but_uncut` warning prints with the ids filled in.

    ⛔ THE FLOOR IS THE JOURNAL'S **COUNT**, and that is the seed's own quantity:
    "how many records a replay of this graph's journal enumerates". It is read
    from `public.journal_event` because that is the enumeration the record role
    can see (no grant on `public.graphs`, and the fly-volume JSONL is not
    addressable from an operator's box). `MAX(seq)` is REPORTED BESIDE it and is
    NOT substituted for it: a refused seq leaves a GAP, so `COUNT(*) < MAX(seq)`,
    and a floor of `MAX` would silently pick a number instead of surfacing the
    disagreement. So a gapped graph is REFUSED (`divergence`, both numbers in the
    payload, and the exact remedy command) rather than seeded — #12894's rule, and
    the reason `K refused` is a first-class outcome here.

    ⛔ IDEMPOTENT, ONE TRANSACTION PER GRAPH. A re-run re-seeds monotonically
    (`GREATEST`) or adopts an `active` row as a no-op, so the verb can be run
    twice, or run while a lane is creating graphs. Per graph, not one big
    transaction: a mid-run failure then leaves a CONSISTENT partial state (each
    graph either seeded+armed or untouched) instead of rolling back every graph
    the operator already saw succeed.

    ⛔ `--dry-run` RUNS THE REAL DECISION AND ROLLS IT BACK. Inside a SAVEPOINT
    per graph, so `seed`'s own guard — not a second opinion in Python — decides
    what would be refused, and the run is rolled back, so nothing is written. A
    dry run that predicted outcomes with a hand-written predicate would be the
    second implementation this module's docstring exists to avoid.

    ⛔ `--arm` IS OPT-IN AND KEEPS THE `--acknowledge-cutover` GATE. Seeding alone
    leaves a graph allocating nothing, so a backfill that stops there fixes the
    row and not the leg — but arming is the same per-graph cutover decision that
    `arm` is, with the same acknowledgement.

    ⛔ AND `--arm` READS A SECOND POPULATION (#14587 review P2). The seed pass and
    the arm pass are not the same set: :data:`ra.UNCUT_GRAPHS_SQL` is scoped
    `c.graph_id IS NULL`, so a graph the SEED-ONLY run healed has LEFT it by
    construction. Arming only the uncut set therefore made the sequence this
    verb documents — `backfill`, then `backfill --arm` — a no-op on the second
    run: `considered: 0`, exit 0, every graph still allocating nothing. So when
    `--arm` is given the population is the UNION of the uncut graphs and the
    graphs already in :data:`ra.MODE_SEEDED` (:data:`ra.SEEDED_GRAPHS_SQL`).

    ⛔ THOSE ROWS ARE ARMED, NOT RE-SEEDED, and that is a correctness point, not
    an optimisation. `SEED_SQL`'s guard compares the JOURNAL's `MAX(seq)` with
    the floor — not the cursor's `seq` — so re-seeding an already-seeded graph
    would re-run the divergence guard over a floor decision the operator already
    made, and a graph seeded from the record's own `MAX(seq)` (the remedy
    `backfill` prints for `divergence`) would be REFUSED on a run whose whole
    job was to arm it. `arm` is the only verb the missing half of the state
    needs (`seeded -> active`).
    """
    from tortoise import record_allocator as ra

    dry_run = bool(getattr(args, "dry_run", False))
    arm_them = bool(getattr(args, "arm_graphs", False))
    #: ⛔ RUN-LEVEL REFUSALS, AND THEY ARE NOT THIS LOOP'S TO SWALLOW. Both are
    #: `RecordAllocatorError`s raised by `require_read_committed` inside `seed`
    #: and `arm`/`_transition`, i.e. they describe the CONNECTION, not this graph:
    #: every remaining graph would fail identically. The per-graph tolerance below
    #: exists for a graph whose mode moved under a concurrent actor, and catching
    #: the shared base class without this guard downgraded `IsolationRefused` to
    #: `outcome: unchanged` — so a `serializable`/misconfigured record DSN reported
    #: `considered: N, seeded: 0, refused: 0` and exited **0** over a run that
    #: wrote nothing. That is the same fail-open shape (`exit 0` over graphs that
    #: allocate nothing) this verb exists to remove, and it is why these re-raise:
    #: `main()`'s own handler prints `REFUSED (…)` and returns EXIT_REFUSED.
    run_level_refusals = (ra.IsolationRefused, ra.RetryableAllocationFailure)
    # ⛔ NO ROLLBACK BETWEEN THE SCOPE AND THE PER-GRAPH READS (#13043 review
    # finding B): `scope_tenant` sets a TRANSACTION-LOCAL GUC, and a rollback
    # ends the transaction it lives in — after which every scoped read answers
    # zero rows, i.e. a pre-flight that fabricates the one datum it exists for.
    ra.scope_tenant(conn, args.org_id)
    with conn.cursor() as cur:
        cur.execute(ra.UNCUT_GRAPHS_SQL, (args.org_id,))
        uncut = [row[0] for row in cur.fetchall()]
    already_seeded: set[str] = set()
    if arm_them:
        # ⛔ THE ARM PASS NEEDS THE GRAPHS THE SEED PASS CANNOT SEE (#14587 review
        # P2): a row that EXISTS is no longer `c.graph_id IS NULL`, so the
        # seed-only run's own output is invisible to `UNCUT_GRAPHS_SQL`. Read
        # them in the SAME scoped transaction (no rollback in between: the
        # tenancy GUC is transaction-local).
        with conn.cursor() as cur:
            cur.execute(ra.SEEDED_GRAPHS_SQL, (args.org_id,))
            already_seeded = {row[0] for row in cur.fetchall()}
    # Sorted so the two ordered sources merge into one deterministic walk (both
    # are `ORDER BY graph_id`, and this report is read by an operator).
    graphs = sorted(set(uncut) | already_seeded)

    entries: list[dict[str, Any]] = []
    seeded = refused = armed = 0
    for graph_id in graphs:
        # ⛔ RE-SCOPE EVERY ITERATION, AND THIS IS NOT DEFENSIVE. `scope_tenant`
        # sets a TRANSACTION-LOCAL GUC and `set_config(..., true)` ends with the
        # transaction: the per-graph `commit()` below clears it, and so does a
        # `ROLLBACK TO SAVEPOINT` taken after `seed()` re-scoped inside the
        # savepoint. Without this line, every graph AFTER the first reads
        # `journal_event` under NO tenant — RLS answers ZERO rows — so it reports
        # `journal_count: 0` and is then REFUSED `divergence` by the seed (which
        # re-scopes and sees the truth), with a printed remedy
        # (`--enumerated-floor 0`) that the same guard refuses. The measured
        # shape: an org with 3 uncut graphs heals ONLY the first one per run and
        # is falsely accused of divergence for the rest (#14582 review P1-1).
        ra.scope_tenant(conn, args.org_id)
        with conn.cursor() as cur:
            cur.execute(ra.JOURNAL_MAX_SQL, (args.org_id, graph_id))
            max_row = cur.fetchone()
            # ⛔ `COUNT(*)`, not `MAX(seq)` — see the docstring. Both are reported,
            # so a gap is VISIBLE instead of being resolved by either side here.
            cur.execute(ra.JOURNAL_COUNT_SQL, (args.org_id, graph_id))
            count_row = cur.fetchone()
        journal_max_seq = int(max_row[0]) if max_row else 0
        journal_count = int(count_row[0]) if count_row else 0
        entry: dict[str, Any] = {
            "graph_id": graph_id,
            "journal_count": journal_count,
            "journal_max_seq": journal_max_seq,
            "enumerated_floor": journal_count,
        }
        # The seed's own decision, under a SAVEPOINT: a refusal must not abort the
        # whole run's transaction, and a dry run must be able to undo exactly this
        # graph's work.
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT backfill_graph")
        rearm_only = graph_id in already_seeded
        try:
            if rearm_only:
                # ⛔ ARM-ONLY, NOT A SECOND SEED — see the docstring. The row
                # exists in `seeded`, which is the one state `arm` exists to
                # leave; re-seeding would put this graph back through the
                # divergence guard over a floor the operator already chose.
                armed_seq = ra.arm(conn, org_id=args.org_id, graph_id=graph_id)
            else:
                result = ra.seed(conn, org_id=args.org_id, graph_id=graph_id,
                                 enumerated_floor=journal_count)
                if arm_them:
                    ra.arm(conn, org_id=args.org_id, graph_id=graph_id)
        except ra.SeedRefused as exc:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT backfill_graph")
            refused += 1
            # `|=`, not `dict.update(…)`: the privatisation ratchet
            # (`tools/sdk_privatise.py --check`) counts an ordinary-word
            # `.update(` receiver as untyped residue because `update` is a
            # RETIRED SDK name, so a plain dict merge spelled `.update(` reads as
            # a new swept-name reference. `|=` is PEP 584's dict merge — the same
            # operation, and it cannot be mistaken for a Graph/SDK call.
            entry |= {
                "outcome": "refused",
                "reason": exc.reason,
                "row_max": exc.row_max,
                "enumerated_floor": exc.enumerated_floor,
                # ⛔ THE REMEDY IS A COMMAND, not advice. `divergence` means the
                # record holds a position the journal's enumeration does not
                # reproduce; the operator who has decided the record's max is the
                # right number can seed FROM it — the seed's guard accepts that
                # (`row_max <= floor`), so the printed command works.
                "remedy": (
                    f"seed --org-id {args.org_id} --graph-id {graph_id} "
                    f"--enumerated-floor {journal_max_seq}"
                    if exc.reason == "divergence" else None),
            }
            entries.append(entry)
            continue
        except ra.RecordAllocatorError as exc:
            if isinstance(exc, run_level_refusals):
                # Not this graph's problem — abort the run so `main()` reports it
                # as a refusal instead of a clean exit over unwritten graphs.
                raise
            # `arm` refuses a row that is no longer `seeded` — a concurrent actor
            # moved it between the population read above and this transition.
            # NOT a failure of the run: undo exactly this graph and report it
            # untouched, so the remaining graphs still heal (the per-graph
            # SAVEPOINT contract this verb documents).
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT backfill_graph")
            entry |= {"outcome": "unchanged", "reason": str(exc)}
            entries.append(entry)
            continue
        if dry_run:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT backfill_graph")
            entry["outcome"] = "would_arm" if rearm_only else "would_seed"
            if rearm_only:
                entry["seq"] = armed_seq
            else:
                entry |= {"seq": result.seq, "mode": result.mode,
                          "healed_tail": result.healed_tail,
                          "burned": result.burned}
            if arm_them:
                entry["would_arm"] = True
        elif rearm_only:
            conn.commit()
            entry |= {"outcome": "armed", "seq": armed_seq,
                      "mode": ra.MODE_ACTIVE, "armed": True}
        else:
            conn.commit()
            entry["outcome"] = "seeded"
            entry |= {"seq": result.seq, "mode": result.mode,
                      "healed_tail": result.healed_tail,
                      "burned": result.burned}
            if arm_them:
                entry["armed"] = True
        if rearm_only:
            # Counted as CONSIDERED and ARMED, never as SEEDED: this run wrote no
            # row for it, and a `seeded` count that included it would report a
            # write that did not happen.
            armed += 1
        else:
            seeded += 1
            if arm_them:
                armed += 1
        entries.append(entry)
    if dry_run:
        # Nothing this run wrote survives. Every savepoint was rolled back; this
        # abandons the remaining transaction explicitly, at the point the
        # read-only contract is decided, rather than leaving it to the caller.
        conn.rollback()
    return {
        "org_id": args.org_id,
        "command": "backfill",
        "dry_run": dry_run,
        # The three counts the issue asks for, named as it names them.
        "considered": len(graphs),
        "seeded": seeded,
        "refused": refused,
        "armed": armed if arm_them else None,
        "refusals": [e for e in entries if e.get("outcome") == "refused"],
        "graphs": entries,
        "cannot_see": (
            "a graph that has NEVER been written (no `journal_event` row) — the "
            "record role has no grant on `public.graphs`. A graph the creation "
            "hook reached is not in this population at all; one it MISSED "
            "NEVER appears here: on an armed deployment its record writes are "
            "REFUSED (`cursor_not_active`), so it accumulates no "
            "`journal_event` row however many times it writes. Seed those per "
            "graph with `seed --graph-id … --enumerated-floor 0` then `arm` — "
            "`/health`'s `record.armed_but_uncut` names them"),
        # ⛔ THE HEAL IS NOT INSTANT ON `/health` (#14587 review P2-2). The field
        # is PROCESS state in the WRITER; this verb runs in another process, so a
        # graph healed here keeps reddening that field until it writes again or
        # the writer restarts. Saying it here is the minimum the review asked
        # for; a `/health` re-probe is not the fix (`/health` collects no I/O,
        # #2850).
        "health_clears": (
            "`/health`'s `record.armed_but_uncut` is in-process state in the "
            "writer, so a graph healed by this verb stops appearing there on "
            "its NEXT USABLE WRITE or on a writer restart — not when this "
            "command returns"),
    }


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
                    choices=("status", "seed", "arm", "halt", "reopen", "sweep",
                             "backfill"))
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
                    help="required by `arm` and by `backfill --arm`: confirms the "
                         "global flag, the per-graph scope and the born-uncut "
                         "graphs have been read (see the refusal text)")
    #: `backfill` (#14582): seed `sweep`'s population. `--dry-run` is the mode an
    #: operator reads FIRST — it runs the seed's real decision under a savepoint
    #: and rolls the run back, so the population and the refusals are visible
    #: before anything is written. `--arm` is opt-in because a `seeded` row
    #: allocates nothing, and it keeps `--acknowledge-cutover`'s gate.
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="`backfill` only: exercise the seed and ROLL IT BACK, so "
                         "nothing is written")
    ap.add_argument("--arm", action="store_true", dest="arm_graphs",
                    help="`backfill` only: arm each graph that seeded (requires "
                         "--acknowledge-cutover)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)

    if args.command != "sweep" and args.command != "backfill" and not args.graph_id:
        ap.error(f"{args.command} is graph-scoped — pass --graph-id (only `sweep` "
                 "and `backfill` walk the org)")
    if args.command in ("sweep", "backfill") and args.graph_id:
        ap.error(f"{args.command} walks the WHOLE org — it takes no --graph-id "
                 "(seed and arm a single graph with `seed`/`arm`)")
    if args.command == "backfill" and (
            args.floor is not None or args.journal is not None):
        # ⛔ THE FLOOR IS PER GRAPH AND IS READ FROM THE RECORD (`COUNT(*)` of
        # the graph's own `journal_event` rows). A single stated floor handed to a
        # whole population would seed every graph to one number — the same defect
        # `sweep` refuses by name.
        ap.error("backfill reads each graph's own journal enumeration — it takes "
                 "no --journal/--enumerated-floor; use `seed` for one graph")
    if args.dry_run and args.command != "backfill":
        # A `--dry-run` that a writing verb silently ignored would be a lie about
        # the one thing it exists to promise.
        ap.error("--dry-run is `backfill`'s: it is the only verb that can run a "
                 "population and roll it back")
    if args.arm_graphs and args.command != "backfill":
        ap.error("`--arm` belongs to `backfill` — the single-graph verb is `arm`")
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
    # ⛔ THE ACKNOWLEDGEMENT GATE COVERS `backfill --arm` TOO (#14582): it is the
    # same per-graph ARMED transition as `arm`, applied to a population, so it is
    # the same decision an operator has to make with the same four facts in front
    # of them. `backfill` WITHOUT `--arm` seeds only (a `seeded` row allocates
    # nothing) and needs no acknowledgement.
    wants_arm = args.command == "arm" or (args.command == "backfill"
                                          and args.arm_graphs)
    if wants_arm and not args.acknowledge_cutover:
        _dsn_env, allocate_env = _record_env_contract()
        print(CUTOVER_ACK_TEXT % {"allocate_env": allocate_env,
                                  "prog": ap.prog}, file=sys.stderr)
        _emit(args, {"org_id": args.org_id, "graph_id": args.graph_id,
                     "command": args.command,
                     "refused": "unacknowledged-cutover",
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
        elif args.command == "backfill":
            payload = _cmd_backfill(conn, args)
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
