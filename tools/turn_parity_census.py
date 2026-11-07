#!/usr/bin/env python3
"""The #10017 purge gate: an EXACT per-session parity census, graph vs record.

Read-only on BOTH stores. It never deletes, never writes, never issues a
`DELETE`/`DROP` of any kind — a purge executed from this tool is a bug, not a
feature. What it does is answer the ONE question #10017 gates the destructive
half on, per session, exactly:

⚠️ "Read-only" is a statement about the COMMANDS it issues, and it is now also
a statement about the STORE's graph list: the graph leg refuses a graph the
store does not already hold, instead of creating it (#11112 — a typo'd
`--graph-name` used to leave a stub graph behind). It is NOT a claim that
opening the store is free of side effects. On the EMBEDDED leg the redislite
open rewrites the RDB on EVERY open/close — measured at this head: two
consecutive read-only CLI runs of the same store left it with a DIFFERENT
sha256 each time (and a different size; the exact bytes are version- and
payload-dependent, so they are deliberately not quoted as a figure) — so the
#10017 byte census is taken AROUND a run, never from a copy the run itself
touched. It also leaves a
`<db>.tortoise-construct.lock` beside the store (the suffix is a CONSTANT,
not the graph's name — measured), and a `<db>.settings` that lives
ONLY while the daemon does (measured: present mid-run, GONE after `close()`). A
`--db-path` that does not exist gets an empty 118-byte store file plus that
lock — the file has no graphs in it (`GRAPH.LIST` → `[]`), which is the claim
below, but a typo'd path still leaves FILE residue to explain. On the URI leg
the bind issues no graph write (measured: `GRAPH.LIST` unchanged), but it IS a
connection, and "no graph write" is not "no server-side effect" — the census
never claims the latter.

⚠️ THE URI LEG NEEDS A CREDENTIAL THAT MAY RUN `GRAPH.LIST` (#11113 review P2).
That refusal is proven by LISTING the store (`GRAPH.LIST`, which creates
nothing), and `GRAPH.LIST` is an INDIVIDUALLY GRANTED command — FalkorDB's own
ACL docs grant it separately, and `-@all +GRAPH.QUERY` does not imply it
(`docs.falkordb.com/commands/acl`). So a credential that cannot list graphs is
a REFUSAL on this leg (exit 3, "nothing was created"), never a pass, and the
message names the credential rather than blaming the store.

⚠️ …AND A CREDENTIAL THAT MAY RUN `INFO` AT THE HANDSHAKE (measured). The
refusal the tenant user actually hits is NOT at the presence check: the pinned
client's constructor issues `INFO` (`falkordb/sentinel.py` → `conn.info(…)`),
`-@all +GRAPH.QUERY +GRAPH.RO_QUERY +PING` denies it, and the run dies in the
BIND with `NoPermissionError: … no permissions to run the 'info' command`.
That arm is diagnosed as the credential too — same predicate, same remedy —
because "the graph could not be read" sent the operator after the store. So
the requirement is the pair (`INFO` + `GRAPH.LIST`), and a per-graph tenant
user fails on the first one.

⛔ Do NOT answer that refusal by granting `GRAPH.LIST` to a per-graph tenant
user. That user is minted with `+GRAPH.QUERY +GRAPH.RO_QUERY +PING` over `-@all`
and is expressly forbidden every unkeyed enumeration command — `GRAPH.LIST`
lists EVERY graph in the keyspace, so the grant would let a single-tenant
credential enumerate every other tenant's graph name (`tortoise/acl_graph_users.py`,
#2113/#8617, pinned by `tests/test_acl_graph_users.py`). Run this gate with an
administrative credential instead.

⚠️ REPLACING THE PROBE IS A REOPEN, NOT A FIX. `GRAPH.RO_QUERY` is measured
non-creating (`tortoise/cache_eviction.py`) and IS in the tenant user's grant
set, so a probe on that verb would work for a tenant credential — but #11112
records `GRAPH.LIST` as the presence source for both legs, so swapping it out
is a decision to take back to that issue, not a fix to make here.

    For every turn node the graph holds, does the record hold a row with the
    SAME key and the SAME transcript-bearing fields — so that deleting the
    graph copy loses nothing?

WHY ANOTHER CENSUS (`record_reconcile` exists)
==============================================
`record_reconcile` compares the record against the CACHE projection over node
KEYS, for drift observation (§17.2). This tool compares the GRAPH's turn nodes
against the RECORD's turn rows, FIELD BY FIELD, as a purge gate (#10017): key
equality alone would certify a purge over a record holding the right ids with
the WRONG text — the exact incident shape the issue exists to prevent. The two
instruments share a posture (refuse when a side cannot be read) and nothing
else.

THE POPULATION (what the purge would touch, and how it is identified)
=====================================================================
A **turn node** is the shape the capture writer mints
(`sdk._TURN_WRITE_CYPHER` / `_capture_turn_id`, the ONE derivation #3551):

    (:Point {id: '<session_id>_t<decimal index>', pointKind: 'event',
             is_episodic: true, is_operator: false, speaker, status,
             createdAt, content, content_hash, embedding?})
        <-[:CONTAINS]- (:Session {id: <session_id>})

The identification is STRUCTURAL — `is_episodic = true` AND `pointKind =
'event'` AND `id == session_id + '_t' + <all-decimal suffix>` for a
`CONTAINS`-wired `:Session` — the same rule `sdk._capture_turn_ids` applies
(suffix `isdecimal`, never prefix alone, so the `sec` vs `sec_t0` cross-match
cannot admit another session's turns). No timestamp is needed to date a node,
and nothing here dates one: the census does not need a turn's age, only
whether the RECORD holds an equivalent copy.

⚠️ An armed target's graph is NOT all pre-cutover. `#10000` makes the RECORD
the first sink, but the writer still writes the turn to the GRAPH for every
turn the record did not take — `capture_session` reads each write's outcome
and sends `_unrecorded` through `_graph_write_with_retry` with the warning
"the record did not take N of M turn(s) — writing them to the GRAPH so the
transcript is not lost" (`tortoise/sdk.py`). So an armed graph legitimately
holds POST-cutover turns the record declined, and they are
`missing_in_record` **by construction** — the repair is a backfill into the
record, never a delete. A lane that reads this contract as "every turn node in
an armed graph is pre-cutover" would hand a #10000 fallback turn to the purge
on an assumption about AGE that no leg of this census checks; the verdict
stays correct because parity is by transcript text, not by age, which is
exactly why the premise has to be stated right here.

Nodes that look like turns but fail any leg of the shape are
NOT purge candidates: they are reported as anomalies (orphans, non-decimal
suffixes, cross-wirings, `is_operator` flags) so nothing is silently invisible
to the gate — a census that quietly excluded what it could not classify would
be a number an owner decides on while looking at a smaller graph than exists.

THE READ IS THE DELETE SET, NOT JUST THE TURN SHAPE (#10017's own predicate)
===========================================================================
The purge predicate #10017 records — in its body, in its completion test and in
the `delete`-phase recipe on the issue — is `MATCH (t:Point) WHERE
is_episodic = true`, with **no** kind filter. `is_episodic` is NOT co-extensive
with "turn" in this repo: `graph-scripts/backfill_is_episodic.py` stamps it on
every `(s:Session)-[:CONTAINS]->(p:Point)` (its own docstring: "capture turn +
regex-extracted points"), and `create_point` / `update_point` accept the flag
on any point kind. So the graph leg READS every `is_episodic = true` node —
the delete set itself — and a node that is episodic but not turn-shaped gets
its own anomaly class (`non_turn_point_kind`), reported BY ID and refusing the
census. Reading only `pointKind = 'event'` would make the gate's population a
strict SUBSET of the set the purge deletes: those nodes would be in neither the
comparison nor the anomaly partition, which is exactly the "a smaller graph
than exists" failure above. The turn SHAPE still decides the purge set (what
`would_delete` counts); the wider READ decides what the census may call CLEAN.

**An anomaly REFUSES the census (exit 2).** Excluding a node from the purge set
is not the same as certifying it: the recorded sweep (`is_episodic = true`)
deletes those nodes too, and the census cannot claim parity over a node it
never compared. So `CLEAN` means *measured the whole
population and it all matched* — never *measured part of it and the part
matched*. The alternative (a distinct `CLEAN_WITH_ANOMALIES` exit) would put
the decision of what each anomaly class means back on whoever reads the exit
code; the operator resolves the anomaly, re-runs, and gets a real 0.

TOTALITY OF THE GRAPH READ (why the graph leg is paged — #5062's class)
======================================================================
FalkorDB applies a server-global `RESULTSET_SIZE` (default **10,000**) to a
query's result set, silently — it truncates, it does not error. The population
this gate exists for is the issue's own figure, **~20,000 turn nodes**, i.e.
twice the cap; a single unpaged `RETURN` would compare at most half of it and
report `CLEAN` over the rest. The graph leg is therefore keyset-paged on
`id(t)` (`>= $cursor`, cursor advanced to `last + 1` — the idiom and its
v6-core caveats are `tortoise/hosted_backup.py`'s `_paged_rows`, reused here
rather than re-derived), and the paged read is cross-checked against the
store's OWN aggregate `count(t)`, which is ONE row and therefore cap-immune
(`hosted_backup._independent_counts`, #4515). A shortfall REFUSES with exit 3:
the read is untrusted, so an absence is not established. Each page returns ONE
row per turn node (`collect(DISTINCT s.id)` for the wirings), so a page
boundary can never split a node's rows and lose a wiring.

THE PARITY DEFINITION (field by field — ambiguity here is how a purge becomes
an incident)
=============================================================================
There are TWO record legs, and this definition is per-leg. **Which leg ran is
in the artifact** (`report["record"]["leg"]`), together with
`compared_fields` and `declared_absent`, so a narrower comparison can never
read as the strong one.

  * the **HOSTED** leg (`--record-dsn`) reads `public.point` through the
    tenant-scoped door.
  * the **LOCAL** leg (`--local-record-dir`) reads the per-session JSONL under
    `~/.tortoise/session-events/` — plain `<day>.jsonl` and sealed
    `<day>.jsonl.gz`, in the root **AND** under `archive/` (all four
    shape x directory combinations, as `session_events_drain._candidate_files`
    reads them; a leg that narrowed either axis silently dropped days) —
    rendered into stored text through the writer's
    own `_capture_turn_window` + `_capture_turn_texts_with_redactions`. **This
    is the AUTHORITY for #10017** (owner ruling 2026-11-01, relayed on the
    issue): the record that must provably hold every turn is the local one,
    and a parity census against the hosted store alone would be measuring the
    wrong authority.

For every in-population graph turn node, a record row (`public.point`,
`node_key = t.id`, `is_episodic IS TRUE` — or the local leg's row derived from
the session window) must exist, and:

    COMPARED EXACTLY           graph                     record
    ─────────────────────────────────────────────────────────────
    key                        t.id                      node_key
    text (framed, as stored)   t.content                 content
    speaker                    t.speaker                 speaker
    kind                       t.pointKind ('event')     point_kind
    status                     t.status                  status
    createdAt                  t.createdAt               created_at
    embedding (when the graph holds one)  t.embedding    embedding

    ⛔ `kind`, `status`, `createdAt` and `embedding` are compared on the
    HOSTED leg ONLY. The LOCAL record carries NO such columns — a capture line
    holds `session_id`, `conversation`, `metadata` and attribution stamps
    (`harness`, `machine_id`, `model`), but no per-turn `status`, `kind`,
    timestamp or vector — so on the
    local leg they are DECLARED ABSENT (`_LOCAL_RECORD_DECLARED_ABSENT`,
    surfaced as the `NOT COMPARED` line and `report["declared_absent"]`)
    rather than fabricated from constants and then counted as compared. A
    `declared_absent` name outside `_DECLARABLE_FIELDS` is an invented claim
    in an artifact an owner decides on, and REFUSES. `is_episodic` is the
    population PREDICATE on both sides (the graph leg reads `is_episodic =
    true`, the record leg filters `is_episodic IS TRUE`), so comparing it
    would be vacuous: it is not in `_EXACT_FIELDS` and must not be listed as a
    compared field here.

    ALLOWED TO DIFFER, WITH THE REASON
    ─────────────────────────────────────────────────────────────
    updatedAt                  t.updatedAt               (absent)  — the turn
                               snapshot (`PointAdded.point`) carries no
                               updatedAt, so record `updated_at` is NULL by
                               construction; it is write bookkeeping, not
                               transcript content.
    content_hash               t.content_hash            (no column) — DERIVED,
                               a pure function of content (#2795 D2), refused
                               by the record schema and recomputed on rebuild.
    is_operator                false                     (NULL)      — always
                               false on turns; a turn node carrying true is an
                               anomaly, not a parity field.

    EMBEDDING RULE. On the HOSTED leg the graph stores `vecf32` (IEEE-754
    binary32); the record stores the journal's float list as jsonb (binary64).
    Same length, and each record component equal to the graph's after rounding
    THROUGH binary32 — the storage the graph itself applies — is parity.
    `graph NULL + record present` PASSES (the record holds a superset; the
    purge loses nothing); `graph present + record absent/different` FAILS (the
    vector would be lost). On the LOCAL leg `embedding` is DECLARED ABSENT:
    the capture line holds no vector, and the graph's vector is a pure function
    of `content` (the same rule as `content_hash`), so the transcript is not
    what is being waived — but a per-turn `field_mismatch` on every embedded
    deployment would be a permanent false refusal, so the limit is stated
    rather than emitted as a defect.

    COUNT RULE. Parity is `record ⊇ graph` per key with field equality — NOT
    count equality. A session captured before the cutover and APPENDED to
    after it holds graph nodes for the old window and record rows for the
    whole stream; deleting the graph copies loses nothing. Count equality is
    reported per session as information (it holds exactly when the session
    was never written post-cutover). The issue's "same count" precondition is
    the pre-cutover-frozen case of this rule.

    NO COUNTERPART. A graph turn node with NO record row FAILS the session —
    the answer is a backfill from the graph into the record (#10017's own
    ordering), never a delete. A record row with no graph node is expected
    (post-#10000 captures) and passes.

    This rule is what makes the local leg's declared-absent fields safe: the
    population comes from the GRAPH, so a record that is missing a row —
    including a day the record provably holds no records for — cannot certify
    it. A sealed day that decompresses to zero records is TOLERATED and
    DISCLOSED (`empty_days`, printed as `empty day : …`), because a complete
    verified archive of no records cannot hide a turn; a 0-byte archive is a
    torn write and REFUSES, because the day's only remaining copy is then
    unreadable.

    An empty PLAIN day file is the SAME state and gets the SAME posture, settled
    or newest: the writer (`reflect-hook.writeFallback`) does
    `appendFileSync(filePath, JSON.stringify(record) + "\n")` — create AND write
    in ONE call — so a 0-byte plain file has never held a record, and the seal
    later compresses that very file into the empty archive above. Refusing it
    would flip the verdict after `DEFAULT_KEEP_DAYS` on IDENTICAL provenance,
    i.e. a verdict an owner signs would change with the WALL CLOCK. So a day
    file that exists and is empty cannot hide a turn (the population is the
    graph's) and is disclosed in `empty_days` exactly like the sealed arm. A
    same-day collision between `archive/<day>.jsonl` and the root's own
    `<day>.jsonl` (one queue key per day+shape, root scanned second) is
    FAIL-CLOSED — a session living only in the displaced copy becomes
    `missing_in_record` — and the displaced path is DISCLOSED in
    `skipped_files` rather than silently narrowing the inspected surface.

    A LOCAL SESSION WHOSE ID APPEARS IN TWO WINDOWS (a re-capture or a resume,
    or the same day surviving in two of the four surfaces) has no positional
    place: entry order is the only index the record carries, so a key for it
    would be a guess. It is HELD BACK into `record.duplicate_window` rather than
    refusing the whole record — one resumed session must not shut a gate whose
    only "repair" would be deleting the record lines that clear it (#3870). The
    verdict still cannot reach CLEAN while the GRAPH holds turn nodes for it:
    those sessions refuse BY NAME (`duplicate_window` in `refused_for`, never
    `missing_in_record`, which would send the operator at the graph/backfill)
    and their nodes are `held_back`, never eligible. A duplicate no graph turn
    needs placed stays disclosed and non-blocking — a refusal with no repair
    path is not a guard.

    The POSITIONAL keys are the FILING leg's, not the file's: the consumer
    windows every conversation before POSTing it
    (`session_events_drain._window`: the last `MAX_SESSION_TURNS` turns, with
    blank and non-conforming turns dropped), and the graph numbers what it
    RECEIVED (`sdk._write_capture_turns`). This leg applies that same window
    before enumerating (`record.filed_window` names every session it changed),
    because keying the file's raw list made every over-cap or filtered session
    report a wall of `field_mismatch text` — a permanent false refusal on
    exactly the population this gate exists for.

THE PASS CRITERION AND THE EXIT CODES
=====================================
    0  measured AND parity clean — every session holding in-population graph
       turn nodes has an exact record superset. The gate is OPEN (the purge
       may proceed through the issue's remaining ordering: scratch-graph
       restore proof, journaled delete, verify reads, byte census).
    1  usage / IO error (bad flags, unparseable args, unreadable --json path).
    2  parity FAIL — at least one session is missing record rows or holds a
       field mismatch, OR the graph holds a turn-shaped node the census could
       not place in the compared population (see the anomaly rule above).
       The gate REFUSES; the problems are listed per node and per field. Run
       the backfill / resolve the anomaly, then re-run this census.
    3  the census could not ESTABLISH an absence — either a side could not
       be READ (graph unreachable, record unreachable, tenant scope refused,
       graph read short of the store's own count), or the COMPARISON could
       not be completed over a side that was read fine: the record declared
       an undeclinable field absent, declared a field this census never
       compares, or an episodic node carries an `embeddingText` no leg
       compares. An absence is NOT established in any of these cases — the
       census refuses to certify parity over a side it could not see, over
       part of one, or over a field it did not compare. This is the
       P0-shaped hole (#10024): an unscoped record read returns ZERO rows and
       would "pass" vacuously, so this tool reads through
       `record_projection.scope_connection` — the one prologue that verifies
       the tenant GUC stuck AND that the role cannot bypass RLS — and treats
       its refusal as exit 3, never as "no rows".

       ⚠️ The two halves have DIFFERENT repairs, so read the message and not
       just the code: an unreadable side is retried (reachability, IAM, RLS);
       an uncomparable field is a CODE change to the comparison (select the
       column and compare it). Retrying a `declared_absent`/`embeddingText`
       refusal changes nothing, because nothing about the read was wrong.

THE NEGATIVE ARM (how we know the gate refuses)
===============================================
A gate observed only passing is decoration. What CI pins, in
`tests/test_10017_turn_parity_census.py`, is the comparison core and the CLI
contract: a missing record row, a mismatched field, a lost/length-changed/
component-different vector and every anomaly class each REFUSE with the node
and field NAMED, over the two dictionaries the store legs produce — built by
hand, not read from live stores. A census that could not refuse those is
broken at the core, and that is what these cases hold.

The live end-to-end demonstration (a real capture, the real journal replayed
into a throwaway record, exit 0, then ONE record row removed and exit 2) is a
lane-run artifact on throwaway stores, NOT a CI test, so nothing here should
be read as proof that the live pair was measured. The STORE LEGS are pinned
separately and without stores — `read_record_turns`' scoped door, its SQL
column set and the refusal it raises are asserted against a stubbed
connection, and `read_graph_turns`' paging/count/classification against a fake
`g` — because an unpinned door is how an unscoped read (#10024) would certify
vacuously while every core case stayed green.

Nothing in this tool may be weakened to make a census pass.

USAGE
=====
    # THE AUTHORITY (owner ruling on #10017): the local capture record.
    python tools/turn_parity_census.py \
        --uri 'docker://:@127.0.0.1:16398/tortoise_test_matrix' \
        --local-record-dir ~/.tortoise/session-events \
        [--json out.json] [--sample 5] [--expect-empty]

    # The HOSTED leg (the store the ruling says is NOT the authority):
    python tools/turn_parity_census.py \
        --uri 'docker://:@127.0.0.1:16398/tortoise_test_matrix' \
        --record-dsn 'postgresql://tortoise_record_app:...@host/db' \
        --org-id <org> --graph-id <graph> [--json out.json] [--sample 5] \
        [--expect-empty]

    --uri            the FalkorDB URI of the TARGET graph (or --db-path for
                     an embedded graph). Read-only queries only.
    --db-path        the embedded-graph path, used INSTEAD of --uri. Mutually
                     exclusive with it; `--graph-name` defaults to
                     `tortoise` on this leg.
    --json           write the full artifact (parity, population, record and
                     anomaly sections) to this path. Exit codes are unchanged
                     by it: the file records the verdict, it does not soften
                     a REFUSED into a pass.
    --sample         how many TURN IDS the artifact samples per session in
                     `per_session_sample`, for the operator's eyeball
                     comparison (default 5). It never affects the verdict:
                     the truncation is of a printed sample, never of the
                     compared population.
    --local-record-dir   the per-session capture log directory, e.g.
                     `~/.tortoise/session-events` (REQUIRED when this is the
                     leg you want — there is NO ambient default and no
                     `TORTOISE_FALLBACK_DIR` fallback: the leg is chosen by an
                     explicit flag, so an inherited environment can never
                     silently move the census onto a different authority).
                     THE AUTHORITY for #10017. Reads BOTH SHAPES in BOTH
                     directories — plain `<day>.jsonl` and sealed
                     `<day>.jsonl.gz`, under the root and under `archive/` —
                     exactly as `session_events_drain._candidate_files` does.
                     Both halves matter: a census that saw only the plain
                     files would call every pre-cutover turn
                     `missing_in_record`, and one that narrowed each directory
                     to one shape silently dropped the other two
                     combinations (review P2). An explicit flag WINS over an
                     ambient TORTOISE_RECORD_DSN (an ambient DSN silently
                     changing the authority would measure the wrong store).
                     Compares key/text/speaker and DECLARES ABSENT
                     status/created_at/point_kind/embedding — see the parity
                     definition above; `compared_fields` and
                     `declared_absent` are in the artifact, and the
                     `NOT COMPARED` line says it on stdout.
                     ⛔ The positional keys are the FILING leg's, not the
                     file's: the consumer windows each conversation first
                     (`session_events_drain._window` — the last
                     MAX_SESSION_TURNS turns, with blank/non-conforming turns
                     dropped), so this leg applies that window before
                     enumerating and DISCLOSES every session it changed
                     (`filed window:` line, `record.filed_window`). A session
                     the record holds TWO windows for cannot be placed
                     positionally: it is held back into
                     `record.duplicate_window` and refuses the census only
                     when the graph really holds its turn nodes
                     (`duplicate_window` in `refused_for`).
                     ⛔ A local-leg CLEAN does NOT arm the purge yet. The only
                     irreversible consumer, `tools/purge_legacy_turns.py
                     --execute`, has no local leg and keys its approval
                     fingerprint on the HOSTED record DSN, so it cannot
                     consume this verdict. That is a known scope boundary, not
                     a bug in this tool: the operator runs this census and
                     posts the artifact (which is how #10017's precondition (2)
                     is met), and wiring the authority leg into `--execute` is
                     the purge tool's own change. Stated here so a `CLEAN` on
                     this leg is never read as an armed delete.
    --record-dsn     defaults to TORTOISE_RECORD_DSN. Must connect as a role
                     the tenant scope accepts (the app role) — a superuser/
                     owner DSN is refused with exit 3, because RLS would be
                     off and the read would mean less than it appears to.
    --org-id/--graph-id   the record tenant pair the deployment's writes file
                     under (env fallbacks TORTOISE_RECORD_ORG_ID /
                     TORTOISE_RECORD_GRAPH_ID). Never guessed: a census filed
                     under the wrong tenant compares the graph against an
                     empty record and must fail loudly (it does — every graph
                     node becomes missing_in_record), not silently.
    --graph-name     the graph INSIDE the store (default: the URI's, or
                     `tortoise` for --db-path). It selects the GRAPH leg only
                     — it never scopes the record read; the record's tenant is
                     chosen with --org-id/--graph-id. A wrong --graph-name is
                     checked against the store before any graph is opened or
                     queried — the presence check, which runs first: a name the
                     store does NOT
                     hold exits 3 naming what it does hold, and never becomes
                     a graph. ⛔ That check proves the NAME EXISTS — it does
                     not prove it is the graph you meant. A wrong name the
                     store DOES hold (another tenant's graph of that name, a
                     typo that a previous run created, or a wrong --db-path
                     pointing at another store) is still indistinguishable
                     from a drained graph, and --expect-empty will certify it
                     CLEAN. Pass --expect-empty only when you know THIS graph
                     is the one that was drained.
    --expect-empty   allow a graph holding ZERO turn-shaped nodes to certify.
                     Without it that is exit 3. It does not make a wrong
                     target safe: presence refuses a name the store does not
                     hold, but a name that EXISTS and is not the intended one
                     still certifies under this flag, so it is an armed flag,
                     not a convenience.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first with an UNATTRIBUTED error.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/turn_parity_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/turn_parity_census.py`"
    )

import argparse
import contextlib
import gzip
import json
import os
import re
import struct
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: The all-decimal suffix rule (`sdk._capture_turn_ids` — `isdecimal`, never
#: prefix alone, so `sec` cannot claim `sec_t0_t3` and a `²`-class suffix is
#: excluded rather than unparseable).
_TURN_ID_RE = re.compile(r"^(?P<sid>.+)_t(?P<idx>[0-9]+)$")

#: This artifact's OWN identity, emitted in every report. The census that consumes it
#: (`tortoise/cache_eviction.py::load_parity_report`) REQUIRES the field, because a
#: report naming nothing is attributable to whatever population the caller wants: a
#: hand-written `{"verdict": "CLEAN", ...}` opened a gate that a real report with an
#: unrecognised instrument name could not (#11004 review, gate-fail-open). A literal,
#: not `__file__`-derived: the artifact must name the same instrument wherever it is
#: copied to, and `PARITY_INSTRUMENTS` in the consumer keys on exactly this string.
INSTRUMENT = "tools/turn_parity_census.py"

#: Keyset page size for the graph leg. Must sit well UNDER FalkorDB's
#: `RESULTSET_SIZE` (default 10_000) so a page is never truncated; the
#: independent aggregate count below is the load-bearing guard (#5062's class).
_GRAPH_PAGE_SIZE = 1000

#: ONE row per episodic node: `collect(DISTINCT s.id)` collapses the
#: `CONTAINS` wirings, so a page boundary can never split a node's rows (with
#: a row-per-wiring shape, a node spanning the boundary would lose a wiring and
#: mis-classify a cross-wiring as a clean single-session turn). OPTIONAL MATCH
#: keeps EVERY episodic node in the result — including orphans (no CONTAINS
#: wiring) and cross-wirings — because a census that drops what it cannot
#: classify reports a smaller graph than exists. The predicate is the RECORDED
#: delete predicate (`is_episodic = true`, #10017), NOT the writer's narrower
#: turn shape: `pointKind` is read as a field and classified in Python
#: (`non_turn_point_kind`), so no node in the delete set is outside the read.
#: Paged on `id(t)` with the `>= $cursor` / `last + 1` idiom of
#: `hosted_backup._paged_rows`.
_GRAPH_EPISODIC_CYPHER = (
    "MATCH (t:Point) WHERE id(t) >= $cursor "
    "  AND t.is_episodic = true "
    "OPTIONAL MATCH (s:Session)-[:CONTAINS]->(t) "
    "RETURN id(t), t.id, t.content, t.speaker, t.status, t.createdAt, "
    "       t.pointKind, t.embedding, t.is_operator, collect(DISTINCT s.id), "
    "       t.embeddingText "
    "ORDER BY id(t) LIMIT $batch"
)

#: The STORE's own count — ONE row, so RESULTSET_SIZE cannot truncate it
#: (#4233/#4515). The reference the paged read must not fall short of: a
#: truncated page set is silence, not an error, so without this the census
#: would compare part of the population and call the whole of it clean. Same
#: predicate as the page query, or the cross-check would measure a different
#: set than the read.
_GRAPH_EPISODIC_COUNT_CYPHER = (
    "MATCH (t:Point) WHERE t.is_episodic = true "
    "RETURN count(t)"
)

#: The NARROWED read: the SAME row shape and the same `is_episodic` predicate,
#: selected by an exact `t.id` set. Exists so a destructive caller re-compares a
#: session's parity FIELDS at the moment of its delete (#10483 review, P2-1)
#: through this module's own read and comparison. Unpaged because its id set is
#: the caller's approved plan — and the store's own count is still compared
#: against the rows seen, because an unpaged narrow read is subject to
#: RESULTSET_SIZE exactly as a page is: a silently short one would report a
#: drifted turn as absent from the graph, or as never having drifted.
_GRAPH_EPISODIC_IDS_CYPHER = (
    "MATCH (t:Point) WHERE t.id IN $ids AND t.is_episodic = true "
    "OPTIONAL MATCH (s:Session)-[:CONTAINS]->(t) "
    "RETURN id(t), t.id, t.content, t.speaker, t.status, t.createdAt, "
    "       t.pointKind, t.embedding, t.is_operator, collect(DISTINCT s.id), "
    "       t.embeddingText "
    "ORDER BY id(t)"
)
_GRAPH_EPISODIC_IDS_COUNT_CYPHER = (
    "MATCH (t:Point) WHERE t.id IN $ids AND t.is_episodic = true "
    "RETURN count(t)"
)

#: The record census query's column set — ONE declaration, because the column
#: names are also the contract `read_record_turns` CHECKS against the cursor's
#: own `description`: a renamed/absent `public.point` column would otherwise
#: silently yield zero comparable rows and certify vacuously (the #10024 shape,
#: arriving through the record's schema instead of its scope).
_RECORD_TURNS_COLUMNS = ("node_key", "content", "speaker", "status",
                         "created_at", "embedding", "point_kind",
                         "is_episodic")

#: The record census query — the same predicate family `record_read._turns`
#: uses, corpus-wide: tenant-scoped, `is_episodic IS TRUE`, turn keys only
#: (the anchored shape is re-derived in Python, one rule for both stores).
#: The SELECT list is DERIVED from `_RECORD_TURNS_COLUMNS` and must stay that
#: way: a hand-spelled copy sitting below this one shadowed it (last assignment
#: wins), so the executing statement was no longer the one built from the
#: contract — editing the tuple would have left the query behind, selecting
#: columns the census no longer names. One declaration, and it is the binding.
_RECORD_TURNS_SQL = (
    "SELECT " + ", ".join(_RECORD_TURNS_COLUMNS) + " "
    "FROM public.point "
    "WHERE org_id = %s AND graph_id = %s AND is_episodic IS TRUE"
)

#: Fields compared exactly, as `(graph prop, record column, human name)`.
_EXACT_FIELDS = (
    ("content", "content", "text"),
    ("speaker", "speaker", "speaker"),
    ("pointKind", "point_kind", "pointKind"),
    ("status", "status", "status"),
    ("createdAt", "created_at", "createdAt"),
)

#: The fields a record leg may NEVER decline to compare: they ARE the
#: transcript, so declaring one "absent" would turn this gate into a key-only
#: check and certify a purge over a record holding the right ids with the wrong
#: text — the exact incident shape #10017 exists to prevent. A source that
#: cannot compare them is not a record leg for this gate.
_UNDECLINABLE_FIELDS = frozenset({"content", "speaker"})

#: What the LOCAL record structurally cannot carry (owner ruling, 2026-11-01,
#: relayed on #10017: *"the authority is the local record (the per-session
#: JSONL under `~/.tortoise/session-events/`), not the hosted store"*). Its rows
#: are `{session_id, conversation: [{role, content}], metadata, harness,
#: machine_id, model}` — the transcript and the turn ORDER, nothing else. There
#: is no per-turn `status` (graph write state), no per-turn `created_at` (only
#: the session-level `metadata.capturedAt`), and no vector (the embedder runs on
#: the capture path, not on this file). All four are declared here, by name, so
#: the report can never present the weaker comparison as the strong one — a
#: declared-absent field is printed as NOT COMPARED, never as a pass.
#:
#: `embedding` is on this list deliberately (review finding, P1): a graph turn
#: carries a vector whenever the embedder is installed, and leaving the field
#: compared would emit a per-turn `field_mismatch` on EVERY session of every
#: embedded deployment — a permanent false refusal that reads as a per-turn
#: defect rather than as a limit of the source. The vector is a pure function
#: of `content` (same rule as `content_hash`), so the transcript is not what is
#: being waived. It is still printed as NOT COMPARED.
_LOCAL_RECORD_DECLARED_ABSENT = ("status", "created_at", "point_kind",
                                "embedding")

#: The field names a record leg may declare absent: the compared columns plus
#: `embedding` (compared by `_embedding_parity`, not by `_EXACT_FIELDS`). A name
#: outside this set is an invented claim in an artifact an owner decides on.
_DECLARABLE_FIELDS = frozenset(
    {rcol for _, rcol, _ in _EXACT_FIELDS} | {"embedding"})

#: The SHAPES a local record day file can have. The seal
#: (`tools/seal_session_events.py`, `DEFAULT_KEEP_DAYS = 7`) only ever WRITES
#: `.gz` (into `archive/`), but `tortoise/session_events_drain.py` READS both
#: shapes in BOTH directories (`_candidate_files`: `for directory in (root /
#: "archive", root)`), because a plain file under `archive/` and a `.gz` left in
#: `root` are both readable substrates it must not skip.
_LOCAL_RECORD_SUFFIXES = (".jsonl", ".jsonl.gz")

#: The surfaces the local record is made of. ⛔ FOUR, not two (review P2):
#: the seal MOVES every day older than the window to `archive/<day>.jsonl.gz`
#: and unlinks the plain file, and `tortoise/session_events_drain.py` reads both
#: DIRECTIONS as well for that reason. A leg that read only `*.jsonl` made every
#: archived day silently invisible — every graph turn older than the seal window
#: would report `missing_in_record` (the purge's own pre-cutover population,
#: i.e. the gate could never open) and a session whose first window had been
#: sealed would be placed positionally from half its windows — a guess the
#: duplicate partition below now holds back instead.
#:
#: Reading only `*.jsonl` + `archive/*.jsonl.gz` still left TWO of the four
#: shape x directory combinations invisible — `archive/*.jsonl` and
#: `*.jsonl.gz` — while `record["files"]` reported itself as the record's
#: coverage. The consequence is fail-CLOSED (those sessions' graph turns become
#: `missing_in_record`, exit 3), so never a wrong PASS; but the refusal pointed
#: the operator at the graph/backfill instead of at the unread file, and the
#: artifact understated what was inspected. Enumerated from the suffix list, so
#: a shape cannot be dropped again by editing one tuple.
_LOCAL_RECORD_GLOBS = tuple(
    [f"*{s}" for s in _LOCAL_RECORD_SUFFIXES]
    + [f"archive/*{s}" for s in _LOCAL_RECORD_SUFFIXES])


class CensusError(Exception):
    """The census could not establish an absence (exit 3) — never a parity
    verdict.

    Two arms, with different repairs: a side could not be READ (retry the
    read — reachability, tenant scope, RLS), or the COMPARISON could not be
    completed over a side that read fine (fix the comparison — an
    undeclinable/invented `declared_absent`, or an `embeddingText` no leg
    compares). Neither is a parity failure, so neither may be reported as one.
    """


def _f32(value: float) -> float:
    """Round `value` through IEEE-754 binary32 — the graph's `vecf32` storage."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _embedding_parity(graph_emb, record_emb) -> tuple[bool, str]:
    """The embedding rule stated in the module docstring, as one verdict."""
    if graph_emb is None:
        return True, ""  # record superset: nothing on the graph is lost
    if record_emb is None:
        return False, "graph holds a vector, record holds none"
    if not isinstance(record_emb, (list, tuple)):
        return False, f"record embedding is {type(record_emb).__name__}, not a list"
    if len(record_emb) != len(graph_emb):
        return False, (f"vector length differs (graph {len(graph_emb)}, "
                       f"record {len(record_emb)})")
    for i, (g, r) in enumerate(zip(graph_emb, record_emb, strict=False)):
        try:
            record_component = _f32(float(r))
        except (TypeError, ValueError, OverflowError, struct.error):
            # `struct.pack('<f', 1e300)` raises OverflowError, and `float()`
            # raises it for an integer too large for binary64 — a field-VALUE
            # problem, so it is a MISMATCH (exit 2), never a crash that a
            # caller reads as a flag mistake (exit 1).
            return False, (f"component {i} is not a finite binary32 value "
                           f"in the record")
        if record_component != float(g):
            return False, f"component {i} differs after binary32 rounding"
    return True, ""


def _graph_vector(tid: str, embedding) -> list | None:
    """The graph's stored vector as floats — or a REFUSAL (``CensusError``).

    A `vecf32` read-back is NOT contractually a `list`: the repo's own sanitizer
    (`tortoise/embeddings.py::stamp_journal_embedding`, line 153-158, #5004
    round-3)
    accepts ANY non-string iterable precisely because "a graph read-back
    (`t.embedding` after a `vecf32` write, #5004 round-3) can arrive as a numpy
    array or a driver vector type". Accepting only `list`/`tuple` here refused
    those containers as unclassifiable (exit 3) — which means the census could
    never certify, and this gate could never open, on exactly the graphs where
    vector parity is the thing at risk. So the acceptance rule is the
    sanitizer's: refuse `str`/`bytes` (a sequence of CHARACTERS), `dict` (not a
    sequence of numbers) and anything not iterable at all; `list(...)`
    everything else. A component that still cannot become a float is a refusal,
    never a silent "no vector".
    """
    if embedding is None:
        return None
    if isinstance(embedding, (str, bytes, dict)):
        raise CensusError(
            f"turn {tid}: the graph embedding is "
            f"{type(embedding).__name__}, not a sequence of numbers — it "
            "cannot be classified, and an unclassifiable vector is a refusal, "
            "never a silent 'no vector'")
    try:
        items = list(embedding)
    except Exception as exc:
        # Any exception, not just `TypeError`: a container whose `__iter__`
        # raises anything else would otherwise escape as a traceback, i.e. the
        # exit-1 "usage/IO" code, where the contract is "unclassifiable side ->
        # exit 3". `BaseException` (KeyboardInterrupt/SystemExit) still
        # propagates.
        raise CensusError(
            f"turn {tid}: the graph embedding is "
            f"{type(embedding).__name__}, not a sequence — it cannot be "
            "classified, and an unclassifiable vector is a refusal, never a "
            "silent 'no vector'") from exc
    try:
        return [float(v) for v in items]
    except (TypeError, ValueError, OverflowError) as exc:
        # `OverflowError` for an integer past binary64's range — the record
        # leg already catches it (`_embedding_parity`); a value the leg
        # cannot classify is a refusal on BOTH sides, never a traceback a
        # caller reads as the exit-1 "bad flags" code.
        raise CensusError(
            f"turn {tid}: the graph embedding holds a non-numeric "
            f"component ({exc}) — an absence is NOT established") from exc


def _graph_rows(g) -> list:
    """Every row of the episodic query, keyset-paged on `id(t)`. READ-ONLY.

    A single unpaged `RETURN` is the #5062/#4515 shape: FalkorDB applies a
    server-global `RESULTSET_SIZE` (default 10_000) SILENTLY, and the
    population this gate exists for is ~20_000 turn nodes — so the read would
    stop half way and the census would certify the half it saw. The aggregate
    cross-check in :func:`read_graph_turns` is the second half of the guard.
    """
    cursor = 0
    rows: list = []
    while True:
        page = g.query(
            _GRAPH_EPISODIC_CYPHER,
            params={"cursor": cursor, "batch": _GRAPH_PAGE_SIZE},
        ).result_set
        if not page:
            return rows
        rows.extend(page)
        cursor = int(page[-1][0]) + 1
        if len(page) < _GRAPH_PAGE_SIZE:
            return rows


def empty_graph_target_flag(args: Any) -> str | None:
    """The refusal message for a graph-target flag passed EMPTY, else ``None``.

    ⛔ ONE IMPLEMENTATION, TWO CALLERS. `turn_parity_census` and
    `purge_legacy_turns` both open a graph from the same three flags, and both were
    exposed to the same shell accident: `--uri "$TORTOISE_DB_URI"` with the variable
    UNSET is a set-but-EMPTY value, `read_graph_turns` selects its arm by TRUTHINESS,
    so the run silently read the EMBEDDED store — and because this tool now authors the
    graph identity into the artifact, the report then named a graph on an instance the
    run never touched, which the cache census accepts for a hosted graph of the same
    name (review P2 on #11097, both cycles). `--graph-name ""` is the mirror: it won
    `graph_name is not None`, so the artifact said ``graph: ""`` while the read fell
    back to ``tortoise``.

    An empty flag value is NOT "no flag". Both callers must refuse it BY NAME before
    anything is read, and both say the same thing, because a second copy of this rule
    is a second chance to disagree about it.
    """
    for flag in ("--uri", "--db-path", "--graph-name"):
        value = getattr(args, flag.lstrip("-").replace("-", "_"), None)
        if value is not None and not str(value).strip():
            return (
                f"{flag} was passed but is EMPTY (a shell expansion like {flag} "
                f"\"$UNSET_VAR\" yields nothing). An empty flag value is NOT 'no "
                f"flag': falling through reads the EMBEDDED store instead of the "
                f"target you named, and the report would then name a graph on an "
                f"instance this run never touched. Refusing instead."
            )
    return None


def resolve_graph_name(uri: str | None, db_path: str | None,
                       graph_name: str | None) -> str:
    """The graph this run MEASURED — the name the report must carry.

    ⛔ NOT A SECOND URI PARSER. The URI arm delegates to
    ``tortoise.projection.resolve_db_endpoint``, the canonical derivation
    ``FalkorProjection.from_uri`` itself calls, so the name written into the artifact is
    the name the connection actually addressed. A report naming a graph the read never
    opened is exactly the defect this closes (review P1 on PR #11004), and re-deriving
    the name here with a hand-rolled parse would be the same class one layer down.

    ``--graph-name`` still overrides the URI's own name (multi-tenant isolation, #7886),
    and the embedded store selects the graph by name too, defaulting to ``tortoise`` —
    the same default :func:`read_graph_turns` applies.

    An EMPTY ``uri`` is not treated as "no URI": it is the URI arm, and the canonical
    derivation refuses a degenerate one. Passing the embedded default instead would let
    a shell accident write a graph name onto an artifact measured on a different store
    (review P2 on #11097).
    """
    if graph_name is not None:
        return graph_name
    if uri is not None:
        # ⛔ `is not None`, NOT truthiness: an EMPTY value is not "no URI" (the CLI
        # refuses it by name before reaching here, and a library caller must not get
        # the embedded default for a target it did not ask for). The canonical
        # derivation refuses a degenerate URI rather than inventing a name.
        # The import is GUARDED for the same reason `read_graph_turns` guards its own:
        # a broken/partial install must read as "the graph could not be resolved"
        # (exit 3), never as a traceback a caller mistakes for the exit-1 usage code.
        try:
            from tortoise.projection import resolve_db_endpoint
        except Exception as exc:  # pragma: no cover - import env problem
            raise CensusError(
                f"tortoise import failed ({type(exc).__name__}: {exc})") from exc

        return resolve_db_endpoint(uri).graph_name
    return "tortoise"


def _refuse_unless_graph_present(proj, requested: str | None) -> None:
    """Refuse BEFORE addressing a graph this store does not already hold.

    `FalkorProjection`'s bind is NOT inert: `__init__` runs the schema DDL
    (`_ensure_indexes`) and, unless skipped, an open-time health probe — and the
    first `GRAPH.QUERY` on a graph that does not exist CREATES IT. Measured on
    the embedded leg (#11112): seeding a store with `tortoise` only and then
    binding `tortise` left `GRAPH.LIST` at `['tortoise', 'tortise']`. The caller
    here therefore binds with `skip_health_check=True, ensure_indexes=False`,
    which issues no `GRAPH.QUERY` (the version probe's `INFO`/`MODULE LIST` may
    still run, and the embedded leg rewrites its own RDB on open — see the
    module docstring), and only then asks.

    Presence is asked of the STORE (`GRAPH.LIST`, which creates nothing). The
    name asked about is the projection's OWN resolved name, not the caller's
    argument: a redirect or a test-name derivation can change which graph is
    addressed, and refusing on the argument would refuse a graph the store
    actually holds.

    A store that cannot be listed is a refusal, never a skip — in a gate, "I
    could not ask" is not "it is there".

    ⛔ A REFUSED LISTING IS DIAGNOSED AS THE CREDENTIAL, NOT THE STORE (#11113
    review P2). `GRAPH.LIST` is an individually-granted command, and the repo's
    own per-graph tenant user is forbidden it by design, so "the store could
    not be listed" sent an operator after the wrong cause — the store was
    healthy and their credential was scoped to one graph. The two refusals stay
    distinct: which one fired is the difference between "check the deployment"
    and "change the credential". Both are fail-closed and both create nothing.

    The classification is the ACL layer's OWN predicate, imported rather than
    respelled (`tortoise.acl_graph_users._is_permission_refusal` — the module
    that mints the per-graph user, so the rule that defines the grant and the
    rule that diagnoses its refusal cannot drift apart). Measured on the pinned
    client (`redis` 7.4.1): the vendor parser maps every `NOPERM` wording —
    `NOPERM this user has no permissions …`, Redis 7's `NOPERM User <u> has no
    permissions …`, and the key-scoped form — onto
    `redis.exceptions.NoPermissionError`, whose message the predicate matches.
    It is NOT a `ConnectionError` test: on this client `AuthenticationError`
    and `AuthorizationError` SUBCLASS `ConnectionError`, so a transport check
    would classify an ANSWERED refusal as an outage (`tortoise/retry.py`
    records that trap from its own incident, #8827).
    """
    # ⛔ PRESENCE-NOT-TRUTHINESS, ON THE NAME ITSELF (#11113 review, P2).
    # A falsy name is NOT "no name": an `or`-chain here substituted a DIFFERENT
    # name for it — `FalkorProjection(path, graph_name="")` keeps
    # `_graph_name == ""` (measured), and `resolve_db_endpoint(uri, "")`
    # returns `""`, so the guard fell through to `requested` / `"tortoise"`,
    # verified a graph this run would NOT open, PASSED, and the read then
    # created the nameless one: `GRAPH.LIST` went `['tortoise']` →
    # `['tortoise', '']`. That is the #11112 defect one layer down, with this
    # function's own contract ("asked about the projection's OWN resolved
    # name") inverted. Neither an empty nor an absent name can be verified, so
    # both REFUSE before the handle is addressed — and `read_graph_turns` must
    # therefore hand this bind the name it was GIVEN. Its embedded branch used
    # `graph_name or "tortoise"`, which coerced a caller's `""` into a
    # DIFFERENT graph before this guard could refuse it (measured: the read
    # returned `tortoise`'s nodes while `resolve_graph_name` named `""` in the
    # artifact). The coercion is now `is not None`, so only an ABSENT name
    # (the documented default) is defaulted and an EMPTY one is refused here.
    wanted = getattr(proj, "_graph_name", None)
    if wanted is None:
        wanted = getattr(proj, "graph_name", None)
    if wanted is None:
        wanted = requested
    if not wanted:
        raise CensusError(
            f"the graph this run would open has no NAME ({wanted!r}) — so its "
            "presence cannot be established, and the first query on a nameless "
            "graph CREATES one. Refused before the handle was addressed "
            "(#11112); nothing was created.")
    try:
        present = sorted(str(n) for n in proj.db.list_graphs())
    except Exception as exc:
        try:
            from tortoise.acl_graph_users import _is_permission_refusal
            refused_by_credential = _is_permission_refusal(exc)
        except Exception:  # pragma: no cover - import env problem
            # An unimportable classifier must not turn a refusal into a pass or
            # into a crash: the generic arm below still REFUSES, it just cannot
            # say whose problem it is.
            refused_by_credential = False
        if refused_by_credential:
            raise CensusError(
                f"this credential may not run GRAPH.LIST ({type(exc).__name__}: "
                f"{exc}) — the command this gate uses to prove {wanted!r} "
                "already exists was REFUSED, so an absence is NOT established "
                "and the graph was not opened (nothing was created). Run this "
                "gate with a credential that may list graphs. Do NOT grant "
                "GRAPH.LIST to a per-graph tenant user: it is unkeyed, so the "
                "grant would let that credential enumerate every tenant's "
                "graph name (#2113/#8617, tortoise/acl_graph_users.py).") from exc
        raise CensusError(
            f"the store could not be listed ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc
    if wanted in present:
        return
    raise CensusError(
        f"the graph {wanted!r} does not exist in this store — refused BEFORE "
        f"opening it, so no graph was created (#11112). The store holds: "
        f"{present or '[]'}. A wrong --graph-name / --db-path is NOT the same "
        "thing as a drained graph, and this gate does not create the target "
        "it certifies")


def read_graph_turns(uri: str | None, db_path: str | None,
                     graph_name: str | None, *,
                     node_ids: Iterable[str] | None = None,
                     g=None) -> dict:
    """The graph leg: every EPISODIC node (the delete set), classified.

    READ-ONLY. Returns ``{"nodes": {id: node-dict}, "unidentifiable": [...]}``.
    The read population is `is_episodic = true` — the predicate #10017 records
    for the purge — so a node that is episodic but not turn-shaped still comes
    back and is classified (`non_turn_point_kind`) instead of being invisible
    to a gate that would delete it. Raises :class:`CensusError` when the graph
    cannot be reached or answered, when the paged read comes back SHORT of the
    store's own aggregate count (RESULTSET_SIZE truncation — an absence would
    not be established), or when a row cannot be classified (an unparseable
    vector is a refusal, never a silent "no vector").

    ``node_ids`` narrows the read to an exact `t.id` set, the same shape and the
    same predicate, and exists so a DESTRUCTIVE caller can re-compare a
    session's parity FIELDS at the moment of its delete (#10483 review, P2-1)
    — through this module's one read and one comparison, never a second
    spelling of either. `read_record_turns(node_keys=…)` is the mirror on the
    record side; a re-verify that only re-checked KEY PRESENCE left the class
    this gate refuses at plan time (a field rewritten after the census) open at
    delete time. An empty `node_ids` reads nothing and answers nothing.

    ``g`` is an already-bound guarded write handle to read through, so a caller
    that is about to DELETE from a graph compares the store it deletes from
    (the `FalkorProjection` it already holds) rather than a second connection
    it hopes addresses the same one. `None` derives it from
    `uri`/`db_path`/`graph_name` exactly as before.
    """
    ids: list[str] | None = None
    if node_ids is not None:
        ids = [str(i) for i in node_ids]
        if not ids:
            return {"nodes": {}, "unidentifiable": []}
    if g is None:
        try:
            from tortoise.projection import FalkorProjection
        except Exception as exc:  # pragma: no cover - import env problem
            raise CensusError(
                f"tortoise import failed ({type(exc).__name__}: {exc})") from exc
        try:
            if uri:
                proj = FalkorProjection.from_uri(
                    uri, graph_name=graph_name,
                    skip_health_check=True, ensure_indexes=False)
            else:
                # `--graph-name` is a TARGET selector, not a URI-only flag: the
                # embedded store selects the graph by name too. Dropping it
                # here censused the default `tortoise` graph while the operator
                # asked for another — a wrong-target read that still reported a
                # verdict.
                #
                # `skip_health_check=True, ensure_indexes=False`: this tool
                # CERTIFIES a store, so its bind must issue nothing (#11112).
                # Both defaults write — the health probe and the schema DDL are
                # `GRAPH.QUERY` statements, and on a graph that does not exist
                # the first one creates it.
                proj = FalkorProjection(
                    db_path,
                    # ⛔ `is not None`, NOT truthiness (#11113 review, P3): an
                    # EMPTY name is a name the caller supplied, and coercing it
                    # to `tortoise` opened a DIFFERENT graph than the one the
                    # artifact then reported (`resolve_graph_name` returns
                    # `""`). Only an ABSENT name is the documented default.
                    graph_name=(graph_name if graph_name is not None
                                else "tortoise"),
                    skip_health_check=True, ensure_indexes=False)
        except Exception as exc:
            # ⛔ A REFUSED BIND IS DIAGNOSED AS THE CREDENTIAL, LIKE A REFUSED
            # `GRAPH.LIST` (#11113 review, P3). Measured: the per-graph tenant
            # user this docstring names is denied `INFO`, which the pinned
            # client's constructor issues BEFORE any graph command — so the run
            # never reaches the presence check and used to report "the graph
            # could not be read", sending the operator after a healthy store.
            # Same predicate as the listing arm, imported (not respelled) for
            # the same reason: one rule, one spelling.
            try:
                from tortoise.acl_graph_users import _is_permission_refusal
                refused_by_credential = _is_permission_refusal(exc)
            except Exception:  # pragma: no cover - import env problem
                refused_by_credential = False
            if refused_by_credential:
                raise CensusError(
                    f"this credential was REFUSED while opening the graph "
                    f"({type(exc).__name__}: {exc}) — an absence is NOT "
                    "established and nothing was created. A per-graph tenant "
                    "user is denied the commands the client issues BEFORE any "
                    "graph command (INFO at the handshake, then the GRAPH.LIST "
                    "this gate uses to prove presence), so it fails here rather "
                    "than at the presence check: run this gate with a "
                    "credential that may run INFO and GRAPH.LIST. Do NOT widen "
                    "the tenant user's grant — it is deliberately kept off "
                    "every unkeyed enumeration command (#2113/#8617, "
                    "tortoise/acl_graph_users.py).") from exc
            raise CensusError(
                f"the graph could not be read ({type(exc).__name__}: {exc}) — "
                "an absence is NOT established") from exc
        # Presence BEFORE any query on the selected graph: the query is what
        # creates it. A caller that already holds `g` is exempt by construction
        # — it is addressing a store it can already read.
        _refuse_unless_graph_present(proj, graph_name)
        # ⛔ A LOST STORE IS NOT A DRAINED ONE (#11113 review cycle 4, P1).
        _refuse_if_the_store_looks_LOST(proj)
        g = proj.g
    try:
        if ids is None:
            rows = _graph_rows(g)
            total = int(g.query(_GRAPH_EPISODIC_COUNT_CYPHER).result_set[0][0])
        else:
            rows = g.query(_GRAPH_EPISODIC_IDS_CYPHER,
                           params={"ids": ids}).result_set
            # The same aggregate cross-check the paged read gets: a narrowed
            # read is still subject to RESULTSET_SIZE, and a silently short one
            # would report a turn as ABSENT FROM THE GRAPH (or as never having
            # drifted) on a truncated read.
            total = int(g.query(_GRAPH_EPISODIC_IDS_COUNT_CYPHER,
                                params={"ids": ids}).result_set[0][0])
    except CensusError:
        raise
    except Exception as exc:
        raise CensusError(
            f"the graph could not be read ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc

    nodes, unidentifiable = _classify_graph_rows(rows)

    # Every node the store counts is either classified or reported as
    # unidentifiable; a shortfall beyond that is a torn/truncated read.
    seen = len(nodes) + len(unidentifiable)
    if seen < total:
        raise CensusError(
            f"the graph read returned {seen} but the store counts {total} "
            "episodic node(s) — the read was TRUNCATED (RESULTSET_SIZE) or "
            "torn. Refusing to certify parity over a population it did not "
            "see: an absence is NOT established")
    return {"nodes": nodes, "unidentifiable": unidentifiable}


#: A lost-store log bigger than this is treated as REPLAYABLE without being read.
_LOST_LOG_MAX_BYTES = 64 * 1024 * 1024


def _graph_total_node_count(g) -> int:
    """How many nodes the graph holds, of ANY label.

    Distinct from the CENSUS population on purpose: `is_episodic` counts the
    delete set, and the lost-versus-drained question is about the whole graph.
    """
    return int(g.query("MATCH (n) RETURN count(n)").result_set[0][0])


def _refuse_if_the_store_looks_LOST(proj) -> None:
    """Refuse the LOST-STORE shape, which `--expect-empty` must never bless.

    ⛔ #11113 review cycle 4, P1. The write-free bind (`skip_health_check=True`)
    is load-bearing — the open-time probe replays the adjacent event log, which
    would RESURRECT purged turns — but the probe was also a DETECTOR, and
    `recover_from_log` draws the line precisely (`tortoise/consistency.py`):
    it rebuilds only when the graph has **0 TOTAL nodes**, there is **exactly
    one** adjacent `.jsonl` (two is "ambiguous", and it refuses), and that log
    holds **at least one parsable event**. This arm applies the SAME three
    conditions, so that "recovery would have replayed this" is a measured
    statement rather than a plausible one (#11113 review cycle 5, P2).

    So the two states ARE distinguishable:

      * a PURGED graph keeps its non-turn nodes (the bind's `(:Meta)`, any
        neighbouring Points) — total > 0 — and the emptiness verdict is the
        legitimate #10017 post-purge state. This arm does not touch it.
      * a LOST graph has ZERO nodes of any label while a replayable log sits
        beside it (redislite starts fresh on a corrupt RDB, an interrupted
        restore, a manual deletion). Pre-PR, the bind replayed it and the
        census then refused; with the probe skipped it read as "drained" and
        `--expect-empty` certified it CLEAN — a false PASS about a store that
        lost its graph.

    Embedded leg only, decided BEFORE any query (#11113 review cycle 5, P3):
    the store's own DIRECTORY decides that, because a URI projection carries no
    `_path`, and asking first is what keeps every remote run free of an
    unbounded `MATCH (n)` scan. (Derived from `_path` rather than from
    `_find_local_jsonl_dir`, which answers None when the directory holds no
    `.jsonl` at all — exactly the pending-sidecar case below, #11113 review
    cycle 7.) ⛔ "I COULD NOT ASK" IS NOT "NOT LOST" (#11113 review cycle 11,
    P1). This arm's whole claim is that it applies `recover_from_log`'s three
    conditions, and recovery's FIRST one is an explicit refusal of an
    unanswerable count (`if db_count is None: … "graph unresponsive —
    recovery requires a live DB"`). Leaving the count to the read arm instead
    does not work: that arm runs a DIFFERENT query over a DIFFERENT population
    (`MATCH (t:Point) WHERE t.is_episodic = true`), so it can succeed where the
    all-label aggregate failed, and a 0-node store then certified CLEAN under
    `--expect-empty` — the same false PASS this arm exists to prevent.
    """
    path = getattr(proj, "_path", None)
    if not path or str(path) == ":memory:":
        # A URI leg has no `_path`; an IN-MEMORY store has one but no directory
        # (deriving one from it would point at the process CWD — measured: it
        # refused a `:memory:` store over an unrelated `.jsonl` in the working
        # directory, #11113 review cycle 10, P3). Recovery never applies to
        # either.
        return
    try:
        events_dir = os.path.dirname(
            os.path.abspath(os.path.expanduser(str(path))))
    except (OSError, ValueError):
        return
    # ⛔ ORDER IS PART OF THE MIRROR (#11113 review cycle 9, P2): the node count
    # comes FIRST, because `recover_from_log` returns early on `db_count > 0`
    # ("graph already has nodes — no rebuild") and therefore never looks at the
    # records at all. Probing them first made an untrustworthy sidecar refuse a
    # HEALTHY store — the #10017 pre-purge census — over a file recovery would
    # not even read, and the message invited the operator to delete a rescue
    # file. Only a 0-node graph asks what recovery would act on.
    try:
        total = _graph_total_node_count(proj.g)
    except Exception as exc:
        # ⛔ AN UNANSWERABLE COUNT REFUSES (#11113 review cycle 11, P1).
        # `recover_from_log` returns `{"recovered": False, … "reason": "graph
        # unresponsive — recovery requires a live DB"}` on exactly this state,
        # so mirroring its conditions means refusing here too. Returning
        # instead delegated the diagnosis to the read arm — a different query
        # over a different population that can succeed while this one fails —
        # and the store then certified CLEAN. Measured red-first: patching only
        # the count query to raise returned `nodes={}` and exit 0.
        raise CensusError(
            "the graph's own total-node count could not be read "
            f"({type(exc).__name__}: {exc}) — so whether this store is DRAINED "
            "or LOST is UNKNOWN, not answered, and --expect-empty must not "
            "certify it. tortoise's own recovery refuses on this same state "
            "('graph unresponsive — recovery requires a live DB'). Check that "
            "the store is reachable and retry (#11113 review cycle 11).") from exc
    if total:
        return                    # non-empty graph: drained, not lost
    # 0 nodes: ask what recovery would act on, in ITS order — the pending
    # sidecar first (the loader can REFUSE it as untrustworthy, which recovery
    # propagates), then the log.
    snapshot = _pending_prewipe_snapshot(events_dir)   # may raise: untrustworthy
    log = _one_replayable_log(events_dir)
    # ⛔ THE LOG COUNT, not `log is None`, decides the diagnosis (#11113 review
    # cycle 10, P2): `_one_replayable_log` answers None for "no logs" (recovery
    # rebuilds from a pending snapshot) AND for "2+ logs" (recovery REFUSES them
    # as ambiguous), and those two states need opposite sentences.
    logs = _jsonl_names(events_dir)
    if snapshot is None and log is None:
        return                    # recovery would refuse this record set too
    record = snapshot or log
    if snapshot is not None and len(logs) > 1:
        why = ("tortoise's own recovery REFUSES to auto-rebuild here — a pending "
               f"pre-wipe snapshot plus {len(logs)} adjacent JSONL logs is an "
               "ambiguous log set (#2943/#428)")
    elif snapshot is not None:
        why = ("tortoise's own recovery REBUILDS from that pending pre-wipe "
               "snapshot — it is the only surviving record of a rebuild's "
               "graph-only Points (#2943)")
    else:
        why = ("tortoise's own recovery ACTS on that log — it replays it, and "
               "refuses it outright when the log carries a torn tail it cannot "
               "replay (#3316); either way it never treats the log as absent")
    kind = "pre-wipe snapshot" if snapshot is not None else "event log"
    raise CensusError(
        "the graph EXISTS and holds ZERO nodes of ANY label, while the "
        f"{kind} {record!r} beside the store is a record it could be "
        "rebuilt from — that is the LOST-STORE shape (a fresh redislite "
        "daemon on a corrupt RDB, an interrupted restore, a manual delete), "
        f"not a drained graph: {why}. An empty graph with such a record is "
        "NOT evidence of a drained graph, so --expect-empty must not certify "
        "it. This gate never recovers — a replay here would "
        "resurrect purged turns (#10017). Restore or rebuild deliberately "
        "(python -m tortoise rebuild --dir <log-dir> --db <db>) and re-run; if "
        "the store really is meant to be empty, remove or archive that record "
        "first so the state is unambiguous.")


def _jsonl_names(events_dir: str, *, strict: bool = False) -> list[str]:
    """The `*.jsonl` entries in `events_dir`, sorted.

    Answers `[]` on error, EXCEPT under `strict=True`, where only
    `FileNotFoundError` answers `[]` and every other listing failure RAISES
    `CensusError` (see below).

    ⛔ `strict=True` re-raises instead of answering `[]` (#11113 review cycle 9,
    P2). `[]` means "no logs here", and one caller refuses on that answer — so
    an UNLISTABLE directory must not be indistinguishable from an empty one.
    The directory is derived from the store's own path (the store exists in it),
    so it is expected to be there; when it cannot be LISTED, that is "I could
    not ask". The lenient default exists for the DIAGNOSIS callers, which only
    word a message and never decide a verdict.
    """
    try:
        return sorted(n for n in os.listdir(events_dir) if n.endswith(".jsonl"))
    except FileNotFoundError:
        return []                 # the directory is gone: nothing is adjacent
    except OSError as exc:
        if strict:
            raise CensusError(
                f"the event-log directory beside the store ({events_dir!r}) "
                f"could not be LISTED ({type(exc).__name__}: {exc}) — so "
                "whether a replayable log sits there is UNKNOWN, not "
                "answered. An unreadable directory is not an absent one, so "
                "--expect-empty must not certify a graph whose replayability "
                "this gate could not establish.") from exc
        return []


def _one_replayable_log(events_dir: str) -> str | None:
    """The single `.jsonl` recovery would replay from `events_dir`, else None.

    Mirrors `recover_from_log`'s preconditions rather than approximating them:
    exactly ONE `*.jsonl` (two is AMBIGUOUS and recovery refuses), and at least
    one PARSABLE event in it (an empty or garbage log is "nothing to recover").
    "Parsable" means what `tortoise/log.py::read_all` means — `json.loads`
    succeeded, any JSON value — NOT "a dict" (#11113 review cycle 6, P3): being
    stricter than the reader would leave this arm silent where recovery still
    attempts a replay.

    A FIFO cannot hang it, and a SYMLINK is followed (#11113 review cycle 6,
    P2/P3). The two hardening bits that matter are `O_NONBLOCK` and `S_ISREG`
    (measured: `open(fifo, O_RDONLY|O_NONBLOCK)` returns a fd in ~0.1 ms and
    fails `S_ISREG`). `O_NOFOLLOW` is deliberately NOT used: recovery follows a
    symlinked `.jsonl` and replays it, so refusing to look through that link
    made this arm FAIL OPEN — measured end-to-end, a 0-node store whose log was
    a symlink certified `CLEAN` (rc 0) while the identical file under a regular
    name refused (rc 3). A link to a FIFO is still rejected, by `S_ISREG` on
    the fd.

    Bounded: a file larger than `_LOST_LOG_MAX_BYTES` counts as REPLAYABLE
    without being read — the conservative direction, because a huge log does
    hold events and reading it on a shared directory is how a certification run
    exhausts memory.

    The decoded text is read line by line and each line is decoded STRICTLY,
    like `read_all` (#11113 review cycle 9, P3): a record carrying an invalid
    UTF-8 byte is "0 events, torn tail" to the reader, so decoding the whole
    file with `errors="replace"` made this arm refuse where recovery does
    nothing.

    ⛔ AND THE LOADER'S THIRD ANSWER IS A REFUSAL HERE TOO (#11113 review
    cycle 9, P2). `None` means "recovery would have nothing to replay", and the
    caller proceeds on it — so it is only ever a genuine NEGATIVE: zero or 2+
    `*.jsonl`, an empty/garbage log, `not S_ISREG` (a FIFO, where "not
    replayable" is the honest answer precisely because a plain open would
    block), or a name whose target VANISHED between the listing and the open
    (ENOENT — a dangling symlink, or a writer rotating the log: the state is
    KNOWN ABSENT there, unlike the EACCES arm below, which is UNASKABLE). A log
    that `os.listdir` has just shown to EXIST but that this
    process cannot OPEN or READ is the third answer, and it was being reported
    here as the second: measured on the parent of this commit, a 0-node store
    with a readable log REFUSED while the identical store with the same log at
    mode 000 RETURNED (`nodes={}`) and certified `CLEAN` — the same false PASS
    the sidecar branch above was just fixed for, one record over. It is also a
    divergence from the predicate this function claims to mirror:
    `recover_from_log` CATCHES that `OSError` and answers
    `{"recovered": False, "reason": "event log unreadable: …"}`
    (`tortoise/consistency.py`), i.e. recovery DECLINES on this state rather
    than replaying it. It raises `CensusError` instead, for the reason the
    presence check states three functions up: in a gate, "I could not ask" is
    not "it is absent".
    """
    import stat

    names = _jsonl_names(events_dir, strict=True)
    if len(names) != 1:           # 0 = nothing; 2+ = ambiguous, recovery refuses
        return None
    path = os.path.join(events_dir, names[0])
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        # The NAME vanished between the listing and the open (a dangling
        # symlink, a log rotated or deleted by the writer that shares this
        # directory). That IS an absence — and the sibling `_jsonl_names`
        # answers `[]` for the same errno one frame up, so refusing here would
        # both over-block and hand the operator a file that does not exist.
        return None
    except OSError as exc:
        # `os.listdir` has just proved this name EXISTS, so an unopenable one is
        # NOT an absence — see the third-answer rule in the docstring.
        raise CensusError(
            f"the single event log beside the store ({path!r}) could not be "
            f"OPENED ({type(exc).__name__}: {exc.strerror or exc}) — so whether "
            "recovery would replay it is UNKNOWN, not answered. An unreadable "
            "log is not an absent one, and tortoise's own recovery REFUSES on "
            "this state rather than proceeding, so this gate refuses too "
            "(#11113 review cycle 9). Inspect the file and make it readable, "
            "or remove / archive it to accept the loss deliberately, then "
            "re-run.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None           # FIFO/device/directory: not a log we can read
        if info.st_size > _LOST_LOG_MAX_BYTES:
            return path           # too big to read, but it certainly has events
        # The dup'd descriptor must not survive a failure in `fdopen` itself —
        # the `with` closes it only AFTER `fdopen` returns one.
        dup_fd = os.dup(fd)
        try:
            with os.fdopen(dup_fd, "rb") as fh:
                dup_fd = None     # `fdopen` took ownership; the `with` closes it
                raw = fh.read()
        finally:
            if dup_fd is not None:
                os.close(dup_fd)
    except OSError as exc:
        # The same third answer, one frame down: a read that DIES mid-file is
        # "could not ask", not "nothing to replay". The handler also covers the
        # HANDLE preparation around the read (`fstat`, `dup`, `fdopen`, e.g. an
        # `EMFILE`), so the message names both and does not claim a read that
        # may not have been attempted; the refusal is right either way.
        raise CensusError(
            f"the single event log beside the store ({path!r}) could not be "
            f"READ to completion, or its handle could not be prepared "
            f"({type(exc).__name__}: {exc.strerror or exc}) "
            "— so whether recovery would replay it is UNKNOWN, not answered. "
            "An unreadable log is not an absent one, so --expect-empty must "
            "not certify a graph whose replayability this gate could not "
            "establish.") from exc
    finally:
        os.close(fd)
    # Mirror `EventLog.read_all`'s READER, not only its event test (#11113 review
    # cycle 7, P2/P3): it normalises a bare `\r` to `\n` (a CR-separated record
    # is a record it replays — measured: the arm was silent, recovery refilled
    # the store, and `--expect-empty` certified it CLEAN), and it parses TEXT,
    # decoding each line STRICTLY (a line with an invalid byte is a torn tail to
    # it, not an event), so `json.loads(bytes)`'s implicit BOM stripping must not
    # make this arm refuse where the reader finds nothing.
    raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    for chunk in raw.split(b"\n"):
        if not chunk.strip():
            continue
        try:
            line = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue              # torn/noise tail: the reader sees no event
        try:
            json.loads(line)
        except ValueError:
            continue
        return path
    return None


def _pending_prewipe_snapshot(events_dir: str) -> str | None:
    """A pending pre-wipe snapshot — the OTHER record recovery replays.

    `recover_from_log` checks the sidecar BEFORE its "no logs" return (#2943):
    with zero `.jsonl` files the snapshot is the only record of a rebuild's
    graph-only Points, and it is replayed. So a 0-node store whose sole record
    is a pending sidecar is the LOST shape too (#11113 review cycle 7, P3), and
    the loader — not `lexists` — is what decides presence.

    ⛔ AND THE LOADER'S THIRD ANSWER IS A REFUSAL, NOT AN ABSENCE (#11113 review
    cycle 8, P2). `_load_prewipe_snapshot` raises for a sidecar that exists but
    cannot be trusted (unparsable, a symlink, a FIFO, absurdly large), because
    it may be the only surviving record; `recover_from_log` turns that into its
    own refusal. Swallowing it into `None` made this arm report "no record here"
    for a state recovery refuses on, and `--expect-empty` then certified it
    CLEAN — the same false-PASS class the arm exists to prevent.
    """
    try:
        from tortoise.consistency import _load_prewipe_snapshot
        from tortoise.projection import prewipe_snapshot_path
    except Exception as exc:      # pragma: no cover - import env problem
        # ⛔ AN UNIMPORTABLE HELPER IS A REFUSAL, NOT AN ABSENCE (#11113 review
        # cycle 11, P2). `None` here means "no record sits beside this store" —
        # the answer that lets a 0-node graph certify as drained. If the loader
        # cannot even be imported we have measured NOTHING, not "nothing is
        # there". The module's own policy agrees: `read_graph_turns`' guarded
        # import raises `CensusError("tortoise import failed …")`, and the
        # neighbouring classifier arm above still REFUSES when its classifier
        # is unimportable.
        raise CensusError(
            "the pre-wipe snapshot check could not be loaded "
            f"({type(exc).__name__}: {exc}) — whether a surviving record sits "
            "beside this store is UNKNOWN, not answered, so an emptiness "
            "verdict here would certify a store whose record set was never "
            "read (#11113 review cycle 11).") from exc
    try:
        path = prewipe_snapshot_path(events_dir)
    except Exception:             # pragma: no cover - path derivation
        return None
    if not os.path.lexists(path):
        return None               # nothing there: the ordinary case
    try:
        pending = _load_prewipe_snapshot(path) is not None
    except Exception as exc:
        raise CensusError(
            f"a pre-wipe snapshot exists at {path} and cannot be trusted "
            f"({type(exc).__name__}: {exc}) — it may be the only surviving "
            "record of a rebuild's graph-only Points, and tortoise's own "
            "recovery REFUSES on this state rather than proceeding. An "
            "emptiness verdict here would certify a store whose surviving "
            "record is unreadable, so this gate refuses too (#11113 review "
            "cycle 8). Inspect the file and repair it, or delete it to accept "
            "the loss deliberately, then re-run.") from exc
    return path if pending else None


def _classify_graph_rows(rows) -> tuple[dict, list[dict]]:
    """The graph rows as `{id: node-dict}` plus the unkeyable ones.

    ONE spelling of the row shape, shared by the full and the narrowed read: a
    second copy would drift from `_GRAPH_EPISODIC_CYPHER`'s column order and
    silently compare a session against the wrong fields.
    """
    nodes: dict[str, dict] = {}
    unidentifiable: list[dict] = []
    for row in rows or []:
        try:
            (_inner_id, tid, content, speaker, status, created_at, point_kind,
             embedding, is_operator, session_ids) = row[:10]
            # #8488 scope 1: `embeddingText` is the LLM rewrite the vector
            # describes when context enrichment is ON. It is NOT reconstructible
            # from the conversation, so it is carried and checked, never assumed
            # away (review cycle 3, P1).
            embedding_text = row[10] if len(row) > 10 else None
        except (TypeError, ValueError) as exc:
            raise CensusError(
                f"the graph returned a row of unexpected shape ({exc}) — "
                "an absence is NOT established") from exc
        if not isinstance(tid, str):
            # NOT dropped: a node with no usable key is still a node the purge
            # would delete, so it is reported under its own class instead of
            # vanishing from both `nodes` and `anomalies` — and instead of
            # masquerading as the TRUNCATED-read diagnosis the count guard
            # would otherwise produce.
            unidentifiable.append({"id": str(_inner_id),
                                   "id_type": type(tid).__name__})
            continue
        vec = _graph_vector(tid, embedding)
        entry = {
            "id": tid, "content": content, "speaker": speaker,
            "status": status, "createdAt": created_at,
            "pointKind": point_kind, "is_operator": is_operator,
            "embedding": vec,
            "embeddingText": embedding_text,
            "sessions": set(),
        }
        for sid in session_ids or ():
            if sid is not None:
                entry["sessions"].add(sid)
        prior = nodes.get(tid)
        if prior is None:
            nodes[tid] = entry
        else:
            prior["sessions"] |= entry["sessions"]

    return nodes, unidentifiable


def read_record_turns(record_dsn: str, org_id: str,
                      graph_id: str,
                      node_keys: Iterable[str] | None = None) -> dict[str, dict]:
    """The record leg: turn rows for the tenant, keyed by `node_key`.

    The connection goes through the repo's own doors — ``record_serve.
    open_record`` (bounded, statement_timeout) then ``record_projection.
    scope_connection`` (the R6 tenant prologue that verifies the GUC stuck and
    REFUSES a role that can bypass RLS). An unscoped read is the #10024 P0:
    it returns zero rows and would certify vacuously; this leg cannot take
    that shape — the scope either holds or the census exits 3.

    ``node_keys`` narrows the read to an exact key set, and exists so a
    destructive caller can re-verify a SESSION's rows immediately before it
    deletes that session (#10483 review, P0 TOCTOU) **through this leg**
    rather than through a second statement of the record query. `None` (the
    census's own call) reads the whole tenant, unchanged.
    """
    # ⛔ AN EMPTY KEY SET IS AN EMPTY READ, AND IT COSTS NOTHING — not even a
    # connection. This decision sat BELOW `open_record` and returned past the
    # `try/finally` that closes it, so the one supported input that reads
    # nothing leaked a bounded record connection every time it was called
    # (#10483 review round 4, P2). Hoisted above the open it needs: with no
    # connection there is nothing to close, nothing to tenant-scope, and no
    # schema to assert about a page of rows that will never be fetched.
    # Normalised ONCE here, so a generator argument cannot be consumed by the
    # emptiness test and then read back as empty by the query.
    keys: list[str] | None = None
    if node_keys is not None:
        keys = [str(k) for k in node_keys]
        if not keys:
            return {"rows_by_key": {}, "non_turn_shaped": 0}
    try:
        import psycopg2  # noqa: F401 - availability probe, record's own driver
    except ImportError as exc:
        raise CensusError(
            f"psycopg2 is not installed ({exc}) — install the 'postgres' "
            "extra; the record cannot be read, so an absence is NOT "
            "established") from exc
    try:
        from tortoise import record_projection, record_serve
    except Exception as exc:  # an unimportable record leg is an unreadable side
        raise CensusError(
            f"the record's modules could not be imported "
            f"({type(exc).__name__}: {exc}) — the record cannot be read, so "
            "an absence is NOT established") from exc
    try:
        conn = record_serve.open_record(record_dsn)
    except Exception as exc:
        raise CensusError(
            f"the record could not be opened ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc
    sql, params = _RECORD_TURNS_SQL, (org_id, graph_id)
    if keys is not None:
        sql = _RECORD_TURNS_SQL + " AND node_key = ANY(%s)"
        params = (org_id, graph_id, keys)
    try:
        cursor = record_projection.scope_connection(conn, org_id)
        cursor.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
    except Exception as exc:
        raise CensusError(
            f"the record could not be read ({type(exc).__name__}: {exc}) — "
            "an absence is NOT established") from exc
    finally:
        with contextlib.suppress(Exception):  # best-effort close
            conn.close()

    # The schema contract: a renamed or dropped `public.point` column would
    # leave every row unkeyed, i.e. an EMPTY comparison that certifies parity
    # vacuously — the #10024 shape arriving through the record's schema instead
    # of its scope. Refuse (exit 3) instead of reading it as "no rows".
    missing = [c for c in _RECORD_TURNS_COLUMNS if c not in columns]
    if missing:
        raise CensusError(
            f"the record query did not return {', '.join(missing)} — the "
            "`public.point` schema the census depends on has moved, so an "
            "absence is NOT established (got: "
            f"{', '.join(str(c) for c in columns)})")

    rows_by_key: dict[str, dict] = {}
    non_turn_shaped = 0
    for row in rows:
        rec = dict(zip(columns, row, strict=False))
        key = rec.get("node_key")
        if not isinstance(key, str) or _TURN_ID_RE.match(key) is None:
            non_turn_shaped += 1
            continue
        rows_by_key[key] = rec
    return {"rows_by_key": rows_by_key, "non_turn_shaped": non_turn_shaped}


def read_local_record_turns(directory, node_keys: Iterable[str] | None = None
                            ) -> dict[str, dict]:
    """The record leg the OWNER named as the authority — the local JSONL.

    Why this leg exists at all. `read_record_turns` reads `public.point` in the
    hosted Postgres record. That is the *hosted* record, and the owner ruled on
    2026-11-01 that **hosted setup is postponed** and that until it is lifted
    *"the authority is the local record (the per-session JSONL under
    `~/.tortoise/session-events/`), not the hosted store"*. #10017 gates an
    irreversible delete on *"the record provably holds every turn the graph
    holds"* — so a census that can only read the hosted store measures a store
    the owner has ruled is not the authority, and on a postponed deployment it
    measures an EMPTY one. This leg reads what he named.

    THE MAPPING, and why it is exact or refused. A local record line is one
    session's captured conversation:

        {session_id, conversation: [{role, content}, ...], metadata, ...}

    ⛔ IT IS THE **FILED** INDEX SPACE, NOT THE FILE'S (review P2). The graph's
    `t<i>` are minted by the server over the list the FILING LEG POSTed
    (`sdk._write_capture_turns` numbers `enumerate(windowed)` at
    `turn_offset=0`), and that list is NOT the file's list: every capture leg
    windows it first — `session_events_drain._window` keeps the last
    `tortoise.quota.MAX_SESSION_TURNS` turns and DROPS any turn that is not a
    dict, whose `role` is not a non-empty `str`, or whose `content` is blank
    after `.strip()`, and the transcript leg applies the same rule
    (`__main__`'s `window_turns`). So for a session over the cap, or with a
    blank turn anywhere, the graph's `sid_t0` holds the **window's** first turn
    while the file's entry 0 is a different turn. Reading the raw list
    positionally therefore emitted a wall of `field_mismatch text` against a
    record that holds every turn — a permanent false refusal on exactly the
    population this gate exists for, misattributing a FILING-leg transform to
    record corruption (and pointing the operator at the graph/backfill, which
    would write the windowed turns into the record under keys that do not
    describe them). This leg therefore applies the SAME window before
    enumerating: `_window(conversation)` → `_capture_turn_window(...)` →
    `_capture_turn_texts_with_redactions(...)`, and `entry i` of THAT list is
    turn `i`, key `f"{session_id}_t{i}"` — the SAME `<session>_t<digits>`
    identity `sdk._capture_turn_ids` derives (one rule, both records). Sessions
    the window actually changed are DISCLOSED (`record["filed_window"]`), so
    the artifact says which keys came from a truncated or filtered list. The
    framed text is built with the writer's OWN frame
    (`sdk._capture_turn_frame`, the single definition #5445/#4897/#5394 all
    route through) and the speaker with the writer's own inverse
    (`sdk._capture_turn_role_text`) — so this leg cannot drift from what the
    writer stored by re-deriving a format.

    WHY A MISALIGNED RECORD CANNOT CERTIFY. A session captured twice appends a
    second window (a `turn_offset`), so a positional map could in principle
    line up the WRONG entry. Every such case fails CLOSED rather than passing
    silently: a window shorter than the graph's turn ids reads as
    `missing_in_record`; and a shifted window mismatches on TEXT, which is a
    refusal. There is no path from a misaligned local record to `CLEAN`.

    A DUPLICATED session id is HELD BACK, not a whole-record refusal (review
    P2). Two windows for one session cannot be placed positionally, but the
    state is a RECOGNISED one (`tools/build_window_transcript.py` refuses it by
    name, and #3870 forbids discarding the record lines that would clear it) —
    so refusing the entire census made the gate unopenable over ONE resumed
    session, with no legitimate exit. Instead the duplicated sessions are
    excluded from the positional rows and listed in their own
    `duplicate_window` partition; `census()` refuses only those sessions that
    the GRAPH actually holds turn nodes for, names the duplicate as the cause
    (never `missing_in_record`, which would misdirect the operator at a
    backfill), and keeps its turn nodes `held_back` so a REFUSED run still
    cannot read as "nothing to delete". Duplicates that no graph node needs
    stay disclosed and non-blocking — a refusal with no repair path is not a
    guard.

    A day present in BOTH shapes (a sealed `.gz` plus a plain file re-created
    after the seal) is read ONCE PER SHAPE, as the drain reads it, because the
    two can hold DIFFERENT windows of one index space. If their session ids
    overlap they land on the duplicate-window partition above — which is
    correct: the leg cannot tell which window is `t0`. If they do not overlap,
    both are needed and both are read. Neither case can reach `CLEAN` wrongly.

    A torn line is CORRUPTION, not a tail to be tolerated — except at the tail.
    An append-only log's newest final line may be mid-write, so it is
    `torn_tail`. Every OTHER unparsable line is DROPPED and named in
    `unparsable_lines`, and it is **never partially recovered**.

    ⛔ WHY DROPPING IS NOT A FALSE PASS (and why it replaced a whole-leg
    refusal — review P1). A merged line really does have an attributable half:
    the trailing record parses and the truncated one does not. Recovering that
    half would certify a session whose sibling record was lost, so this leg
    does not. But REFUSING THE WHOLE LEG is not the safe alternative it looks
    like: the same non-vacuity argument the torn-tail arm and the empty-day arm
    already rest on applies unchanged — **the POPULATION COMES FROM THE
    GRAPH.** A dropped line contributes no keys to `rows_by_key`, so every
    graph turn it may have described is `missing_in_record` -> that session is
    REFUSED. A turn the graph does not hold cannot make a verdict wrong. So
    dropping is fail-closed by construction, while a whole-leg refusal blocks
    every OTHER session the record CAN certify — with no repair path except
    editing audit evidence.

    A line that PARSES but is malformed for this record — not a JSON object, no
    usable `session_id`, no `conversation` key, or a `conversation` that is not
    a list — takes the SAME posture, for the same reason and on stronger
    evidence: `session_events_drain` DEAD-LETTERS every one of those classes
    (`unparseable_record`, `no_session_id`, `no_conversation`), so a malformed
    line NEVER REACHED THE GRAPH and contributes no key to the population. It
    is dropped and named in `malformed_lines` (`malformed  : …` on stdout), and
    any graph turn its session holds is `missing_in_record` -> REFUSED. A
    malformed `conversation` is never coerced to `[]`: an ABSENT transcript is
    not an empty one, so the session is left uncertified rather than read as a
    session that held no turns.

    That distinction is load-bearing and was measured, not assumed: this box's
    authority store held TEN such lines (all lost-newline merges of two
    concurrent appends), so the refusing posture meant the gate #10017 waits on
    could never open on the store the 2026-11-01 ruling names as the authority
    — an instrument that reports nothing is not a guard. Pinned by
    `test_an_interior_unparsable_line_is_DROPPED_disclosed_and_still_FAILS_CLOSED`,
    which asserts the DISCLOSURE and the fail-closed verdict TOGETHER: the
    disclosure alone would not be a safety property.

    The ONE state where dropping is not a disclosure is the record that held
    NOTHING BUT dropped lines: there is no report to name them in, because the
    per-line disclosure is emitted after the read returns. That state is a
    `CensusError` whose message says the record was NOT empty and names the
    first dropped line — an all-dropped record is a damaged record, never an
    empty one. Pinned by
    `test_a_record_of_ONLY_malformed_lines_refuses_NAMING_the_line_not_emptiness`
    and its unparsable sibling.
    """
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise CensusError(
            f"the local record directory does not exist ({root}) — the record "
            "cannot be read, so an absence is NOT established")
    # ⛔ `Path.glob` SWALLOWS every scandir OSError (the seal names this class
    # one surface over, `seal_session_events._unreadable_archives`), so an
    # unreadable directory would be indistinguishable from an empty one and the
    # leg would report a complete read over the surfaces it could see. Enumerate
    # explicitly instead, and refuse.
    # Mirror the substrate's own enumerator (`session_events_drain
    # ._candidate_files`): BOTH shapes in BOTH directories, ONE path per
    # (day, shape) queue key, `root` scanned SECOND so the writer's own file
    # wins over an unusual copy under `archive/` — the seal only ever writes
    # `.gz` there, but it can still read a plain one.
    found: dict[tuple[str, str], Path] = {}
    #: Paths displaced by a same-day, same-shape collision. The verdict stays
    #: fail-closed (a session living only here becomes `missing_in_record`),
    #: but a narrowed surface the artifact does not name is a silent claim of
    #: coverage — the exact class the four-surface enumeration closed.
    skipped_files: list[str] = []
    #: A record-SHAPED path that exists but is not a regular file (a dangling
    #: symlink, a FIFO, a directory named `<day>.jsonl`). A SEPARATE list from
    #: `skipped_files` on purpose: that one is the day+shape COLLISION
    #: disclosure and its stdout sentence states a collision cause, so sharing
    #: it printed a contradictory cause for this class (review P3). Named, never
    #: silent — a narrowed surface the artifact does not name is a silent claim
    #: of coverage.
    unreadable_paths: list[str] = []
    # ⛔ `scan_dir`, NOT `directory`: the loop variable used to reuse the name of
    # this function's own PARAMETER (`directory`, declared in the signature), so
    # every iteration silently destroyed the argument the caller passed. It is
    # inert today (the later reads all use `root`), which is exactly why it is
    # worth removing now — the next edit that reaches for `directory` after this
    # loop would read the root path instead of the caller's value (review P3).
    for scan_dir in (root / "archive", root):
        if not scan_dir.exists():
            # ⛔ AN UNRESOLVABLE SURFACE IS NOT AN ABSENT ONE (review P3).
            # `exists()` follows links, so an `archive/` symlink whose target is
            # gone (an unmounted archive volume, a re-provisioned disk) read
            # exactly like "no archive directory yet" and was skipped in
            # silence — while the artifact went on reporting the four
            # shape x directory surfaces as covered. A GENUINELY missing
            # `archive/` is normal and stays silent; a symlink that does not
            # resolve never is, and it could be holding every sealed day.
            if scan_dir.is_symlink():
                raise CensusError(
                    f"the local record's surface {scan_dir} is a symlink that "
                    "does not resolve — an archive volume that is not mounted "
                    "is NOT an archive that holds nothing, so an absence is "
                    "NOT established")
            continue
        try:
            entries = list(scan_dir.iterdir())
        except OSError as exc:
            raise CensusError(
                f"the local record directory {scan_dir} could not be LISTED "
                f"({type(exc).__name__}: {exc}) — an unreadable surface is "
                "not an empty one, so an absence is NOT established") from exc
        for entry in entries:
            if not entry.is_file():
                # ⛔ A RECORD-SHAPED PATH THAT IS NOT A REGULAR FILE IS A
                # NARROWING OF THE SURFACE, so it is DISCLOSED (review P3).
                # `is_file()` follows symlinks, so a dangling `<day>.jsonl`, a
                # FIFO, or a directory named `<day>.jsonl` was skipped by a bare
                # `continue` and named in NO list: the artifact then reported
                # `files` as full coverage of a surface it had quietly dropped,
                # which is the one thing this leg's own `skipped_files` contract
                # forbids. Only record-SHAPED names are named here; a plain
                # directory (e.g. `archive/`) is not a narrowing.
                #
                # ⛔ ITS OWN LIST, not `skipped_files` (review P3). That list is
                # the COLLISION disclosure and its stdout line states a
                # collision cause per entry, so putting this class there printed
                # a self-contradicting sentence ("not a regular file … same
                # day+shape as a file the root store already supplies") that
                # named the wrong repair. Two causes, two lists, each with its
                # own sentence.
                if any(entry.name.endswith(s) for s in _LOCAL_RECORD_SUFFIXES):
                    unreadable_paths.append(
                        f"{entry.relative_to(root)} (not a regular file, "
                        "so its records were not read)")
                continue
            for suffix in _LOCAL_RECORD_SUFFIXES:
                if entry.name.endswith(suffix):
                    key = (entry.name[: -len(suffix)], suffix)
                    displaced = found.get(key)
                    if displaced is not None:
                        skipped_files.append(str(displaced.relative_to(root)))
                    found[key] = entry
                    break
    files = sorted(found.values())
    # The tail is only tolerance-able on the newest PLAIN day file in the LIVE
    # directory: a `.gz` is written whole and verified by the seal, and a plain
    # file under `archive/` is not an in-flight append either — a torn line in
    # either is corruption.
    plain = [f for f in files
             if f.parent == root and not f.name.endswith(".gz")]
    newest_plain = plain[-1] if plain else None
    if not files:
        raise CensusError(
            f"no record files under {root} ({' / '.join(_LOCAL_RECORD_GLOBS)}) "
            "— the local record is EMPTY, and an empty record is not evidence "
            "of parity (it is the shape that would certify deleting every "
            "turn). An absence is NOT established"
            # ⛔ EMPTY vs DAMAGED (review P3): a record-SHAPED path that exists
            # and could not be read is not an empty record — the same
            # distinction the all-dropped arm above makes for LINES. The report
            # is built after this raise, so this is the only place it can be
            # said; without it an operator is told to check the directory when
            # the truth is an unreadable day file.
            + (("\n\nThe directory is NOT empty of record-shaped paths: "
                + "; ".join(unreadable_paths)
                + " — those were present and NOT read, so this is a DAMAGED "
                  "read, not an empty record.")
               if unreadable_paths else ""))

    # ⛔ THE FILING LEG'S WINDOW (review P2): the graph's `t<i>` are minted over
    # the list that leg POSTed, not over the file's raw list, so `_window` is
    # IMPORTED here — never re-declared. A local copy of "the last 500, drop
    # blanks" that drifted would produce exactly the misaligned key space this
    # leg exists to close.
    try:
        from tortoise.sdk import (
            _capture_turn_role_text,
            _capture_turn_texts_with_redactions,
            _capture_turn_window,
        )
        from tortoise.session_events_drain import _window as _filing_window
    except Exception as exc:  # an unimportable frame is an unreadable side
        raise CensusError(
            f"the SDK's stored-text definition or the filing leg's window "
            f"could not be imported ({type(exc).__name__}: {exc}) — the local "
            "record cannot be rendered into the writer's own stored text, so "
            "an absence is NOT established") from exc

    wanted = None if node_keys is None else {str(k) for k in node_keys}
    conversations: dict[str, list] = {}
    occurrences: dict[str, int] = {}
    parsed = 0
    torn_tail: list[dict] = []
    #: Unparsable lines that are NOT the append-only tip: corruption, a
    #: lost-newline merge of two concurrent appends, a truncated write that a
    #: later append did not complete. DROPPED and DISCLOSED — never partially
    #: recovered (see the module contract's `unparsable_lines`).
    unparsable_lines: list[dict] = []
    #: Lines that PARSE as JSON but are malformed for this record: not an
    #: object, no usable `session_id`, no `conversation` key, or a
    #: `conversation` that is not a list. DROPPED and DISCLOSED, exactly like
    #: `unparsable_lines`, and for the same reason — the two classes the filing
    #: leg refuses (`no_session_id`, `no_conversation`,
    #: `unparseable_record` — `session_events_drain` DEAD-LETTERS them) never
    #: reach the graph, so they contribute NO key to the population. Dropping
    #: one therefore cannot hide a turn, while refusing the whole leg over one
    #: is an unrepeatable false refusal: it blocks every OTHER session the
    #: record CAN certify, with no repair path (#3870 forbids deleting the
    #: audit lines that would clear it). Same posture as round 12, which is
    #: what made this gate openable on the store the ruling names (review P2).
    malformed_lines: list[dict] = []
    #: Sealed days that are PROVABLY empty: a complete, round-trip-verified
    #: gzip stream whose payload holds no records. Disclosed in the artifact —
    #: a tolerance that is not counted is indistinguishable from a day that
    #: went missing.
    empty_days: list[str] = []
    #: Sessions the record holds TWO OR MORE windows for. Held back from the
    #: positional rows and DISCLOSED — a whole-record refusal here made one
    #: resumed session unopenable for the gate (review P2).
    duplicate_window: list[dict] = []
    #: Sessions whose FILED window differs from the file's list (dropped blank/
    #: non-conforming turns, or a tail truncation past MAX_SESSION_TURNS). These
    #: are the sessions whose keys come from a transformed list, so the artifact
    #: says so (review P2).
    filed_window: list[dict] = []
    for path in files:
        try:
            if path.name.endswith(".gz"):
                # ⛔ TWO on-disk states, NOT one (review P1; measured).
                #
                # `gzip.decompress(b"")` returns `b""` WITHOUT raising, and a
                # valid stream of an empty payload also decompresses to `b""`
                # — so these two are indistinguishable BY CONTENT and must be
                # separated by SIZE:
                #
                #  0 bytes        the archive write never completed. The seal
                #                 unlinks the plain source only after a
                #                 verified round-trip (`seal_one`), so a
                #                 0-byte archive means the only remaining copy
                #                 of that day is unreadable -> REFUSE.
                #  20 bytes+      `gzip.compress(b"")` — a COMPLETE archive
                #                 that provably holds no records. Its payload
                #                 cannot hide a turn, so it is tolerated with
                #                 the same posture as the newest plain day and
                #                 DISCLOSED. Refusing it permanently blocked the
                #                 census on a state the seal really does write
                #                 (`eligible()` has no size floor; `seal_one`
                #                 compresses whatever it read), and the block
                #                 could only be cleared by deleting audit
                #                 evidence — a false refusal, not a guard.
                size = path.stat().st_size
                if size == 0:
                    raise CensusError(
                        f"the sealed day {path.name} is ZERO bytes — a "
                        "complete gzip of an empty day is 20 bytes, so this "
                        "archive was never written. The seal removes the "
                        "plain source only after the archive round-trips, so "
                        "the only remaining copy of that day is unreadable: "
                        "an absence is NOT established")
                raw = gzip.decompress(path.read_bytes()).decode(
                    "utf-8", "replace").splitlines()
                if not any(line.strip() for line in raw):
                    empty_days.append(str(path.relative_to(root)))
                    continue
            else:
                raw = path.read_text(encoding="utf-8",
                                     errors="replace").splitlines()
                # ⛔ An empty day is ONE state, not two — and the SAME file is
                # in it for its whole life (review P1). The writer
                # (`reflect-hook.writeFallback`) does `appendFileSync(filePath,
                # JSON.stringify(record) + "\n")`: create AND write in ONE
                # call, so a 0-byte day file has never held a record. The seal
                # then compresses that very file to a 20-byte empty archive
                # (`eligible()` has no size floor, `seal_one` compresses
                # whatever it read) and the `.gz` arm above TOLERATES it. If
                # this arm refused, the identical provenance would flip
                # REFUSED -> tolerated after `DEFAULT_KEEP_DAYS`, i.e. the
                # verdict an owner signs would change with the WALL CLOCK.
                #
                # So the posture is the same here, and it is safe for the same
                # reason as the sealed arm: the POPULATION COMES FROM THE
                # GRAPH. A day that provably holds no records cannot hide a
                # turn — that turn's id is then absent from the record and the
                # session is `missing_in_record` -> REFUSED. The tolerance is
                # non-vacuous, and it is DISCLOSED below rather than silent.
                if not any(line.strip() for line in raw):
                    empty_days.append(str(path.relative_to(root)))
                    continue
        except CensusError:
            raise
        except Exception as exc:
            # A sealed day that cannot be decompressed is an unreadable side —
            # `session_events_drain` treats it the same way. Absence is NOT
            # established for the sessions it may hold.
            raise CensusError(
                f"the local record's file {path.name} could not be read "
                f"({type(exc).__name__}: {exc}) — the record cannot be read, "
                "so an absence is NOT established") from exc
        nonempty = [(n + 1, ln) for n, ln in enumerate(raw) if ln.strip()]
        for pos, (lineno, line) in enumerate(nonempty):
            #: `<file>:<lineno>`, root-relative. Hoisted out of the `except`
            #: below because the malformed-line arms (which run AFTER a
            #: successful parse) name the line too — the first cut of those arms
            #: read a `where` that only existed on the parse-failure path and
            #: raised `UnboundLocalError` on every malformed line.
            where = str(path.relative_to(root)) + f":{lineno}"
            try:
                entry = json.loads(line)
            except Exception as exc:
                at_tail = (newest_plain is not None
                           and path == newest_plain
                           and pos == len(nonempty) - 1)
                if at_tail:
                    # The append-only TIP: a mid-write line the next append
                    # completes, or one a crash cut. Counted, never silent.
                    torn_tail.append({"at": where, "error": str(exc)})
                else:
                    # ⛔ CORRUPTION: dropped and NAMED. The line is NOT
                    # partially recovered — the parseable half of a merged
                    # line describes a session whose sibling record was lost,
                    # so certifying from it would be a false pass. Dropping it
                    # cannot be one either: the POPULATION COMES FROM THE
                    # GRAPH, and a turn whose record line was lost is absent
                    # from `rows_by_key`, so its session is
                    # `missing_in_record` -> REFUSED. See the module
                    # contract.
                    unparsable_lines.append({"at": where,
                                             "error": str(exc)})
                continue
            parsed += 1
            if not isinstance(entry, dict):
                malformed_lines.append({
                    "at": where,
                    "error": f"not a JSON object ({type(entry).__name__})"})
                continue
            # ⛔ THE SUBSTRATE'S RULE, NOT TRUTHINESS (review P2).
            # `session_events_drain` normalises
            # `raw_sid if isinstance(raw_sid, str) and raw_sid.strip() else None`
            # and then DEAD-LETTERS the record, so a WHITESPACE-only id never
            # reaches the graph. Plain truthiness accepted `"   "` as a session
            # named three spaces — a session no door can mint, whose keys can
            # therefore never match a graph turn. One rule, spelled once.
            raw_sid = entry.get("session_id")
            sid = (raw_sid if isinstance(raw_sid, str) and raw_sid.strip()
                   else None)
            if sid is None:
                malformed_lines.append({
                    "at": where,
                    "error": f"no usable `session_id` ({type(raw_sid).__name__})"
                             " — dead-lettered by the filing leg, so it filed "
                             "no turn and can hide none"})
                continue
            conversation = entry.get("conversation")
            if conversation is None:
                # ⛔ ABSENT is not EMPTY (review P2, kept). The writer always
                # emits the array, so coercing a missing key to `[]` would make
                # a LOST transcript read as if the session genuinely held no
                # turns. It is NOT coerced — the line is dropped and named, so
                # any graph turn this session holds reads as
                # `missing_in_record` (REFUSED) rather than as an empty
                # session. An explicit `[]` is real data and stays tolerated.
                malformed_lines.append({
                    "at": where,
                    "error": f"session {sid} carries NO `conversation` key — "
                             "dead-lettered by the filing leg "
                             "(no_conversation), so its transcript never "
                             "reached the graph; NOT coerced to an empty "
                             "session"})
                continue
            if not isinstance(conversation, list):
                malformed_lines.append({
                    "at": where,
                    "error": f"session {sid} `conversation` is "
                             f"{type(conversation).__name__}, not a list"})
                continue
            # ⛔ AN EMPTY FILED WINDOW IS NOT A SECOND CAPTURE WINDOW (review
            # P2). A line whose conversation windows to NOTHING (`[]`, or turns
            # that are blank / not dicts / not `str` roles) contributes no key to
            # `rows_by_key` — and it cannot reach the graph either: the filing
            # leg (`session_events_drain`) windows the same list and DEAD-LETTERS
            # the session as `no_conversation` when the window is empty, so it
            # filed no turn and can hide none. Counting it as an `occurrence`
            # made ONE real window plus one empty line look like two capture
            # windows of one index space: the session was popped from the
            # positional key space and REFUSED as `duplicate_window`, whose
            # repair advice is "resolve which capture is authoritative, by hand"
            # — a false refusal, with a wrong cause, on the exact class the
            # duplicate arm exists to keep open. The empty line is still a real
            # SESSION (`[]` is data, not loss) and stays in `conversations` when
            # it is the only line; it just never counts as a window.
            try:
                filed_turns = _filing_window(conversation)
            except Exception as exc:
                raise CensusError(
                    f"the local record's session {sid} could not be put "
                    f"through the filing leg's window "
                    f"({type(exc).__name__}: {exc}) — without the window the "
                    "graph's positional keys cannot be derived, so an absence "
                    "is NOT established") from exc
            if not filed_turns:
                conversations.setdefault(sid, conversation)
                continue
            occurrences[sid] = occurrences.get(sid, 0) + 1
            conversations[sid] = conversation

    # ⛔ A DUPLICATED session id is HELD BACK, not a whole-record refusal
    # (review P2). `conversations` holds the LAST window per id, and placing it
    # positionally would be a guess dressed as a measurement — but refusing the
    # ENTIRE census over one resumed session also made the gate unopenable
    # (`tools/build_window_transcript.py` refuses the same state by name, and
    # #3870 forbids deleting the record lines that would clear it: there was no
    # legitimate exit). So they leave the positional key space and land in their
    # own partition; `census()` refuses only the ones the GRAPH holds turns
    # for, names the duplicate as the cause, and holds their nodes back.
    duplicated = sorted(s for s, n in occurrences.items() if n > 1)
    for sid in duplicated:
        conversations.pop(sid, None)
        duplicate_window.append({"session": sid,
                                 "windows": occurrences[sid]})

    if not conversations and not duplicate_window:
        # ⛔ TWO DIFFERENT STATES, ONE VERDICT — and the message must name which
        # (review P3). This guard is reached both by a record that held NO
        # records (an empty/whitespace-only day — not evidence of parity, review
        # cycle 4 P2) and by a record whose EVERY line was DROPPED. The second
        # state is not emptiness: the lines were READ and discarded, so the
        # transcripts they described are uncertifiable, and a refusal that says
        # "parsed ZERO sessions / an empty record" reports a healthy empty store
        # over a damaged one. The report object (and the per-line `unparsable :`
        # / `malformed  :` disclosure it feeds) is built AFTER this raise, so the
        # dropped lines can only be named here.
        dropped = ([("unparsable", d) for d in unparsable_lines]
                   + [("malformed", d) for d in malformed_lines])
        if dropped:
            first_kind, first = dropped[0]
            raise CensusError(
                f"the local record under {root} DROPPED every line it held — "
                f"{len(unparsable_lines)} unparsable, {len(malformed_lines)} "
                f"malformed, {len(torn_tail)} torn tail, ZERO placed, from "
                f"{len(files)} file(s). The record is DAMAGED, not empty: every "
                "line was read and discarded, so none of it can be placed "
                "positionally and an absence is NOT established. First "
                "dropped line — "
                f"{first_kind} {first['at']}: {first['error']}")
        raise CensusError(
            f"the local record under {root} parsed ZERO sessions from "
            f"{len(files)} file(s) — an empty record is not evidence of parity, "
            "so an absence is NOT established")

    rows_by_key: dict[str, dict] = {}
    for sid, conversation in conversations.items():
        # ⛔ THE FILED WINDOW, THEN THE STORED TEXT (review P2). Rendering the
        # RAW conversation made `t0` the file's first entry while the graph's
        # `t0` is the FILED window's first entry (`session_events_drain._window`
        # keeps the last MAX_SESSION_TURNS and drops blank/non-conforming
        # turns), so every over-cap or filtered session reported a wall of
        # `field_mismatch text` against a record that holds every turn — a
        # permanent false refusal on exactly the population this gate exists
        # for, blaming the record for the filing leg's own transform.
        try:
            filed = _filing_window(conversation)
        except Exception as exc:
            raise CensusError(
                f"the local record's session {sid} could not be put through "
                f"the filing leg's window "
                f"({type(exc).__name__}: {exc}) — without the window the "
                "graph's positional keys cannot be derived, so an absence is "
                "NOT established") from exc
        if len(filed) != len(conversation):
            # Disclosed, never silent: these keys describe the FILED list, and
            # an artifact that hid the truncation would misdescribe its own
            # coverage.
            filed_window.append({"session": sid,
                                 "record_turns": len(conversation),
                                 "filed_turns": len(filed)})
        # ⛔ THE WRITER'S STORED TEXT, NOT A RE-FRAME OF THE RAW TURN (review
        # finding, P1). What the node stores is
        # `_capture_turn_texts_with_redactions(_capture_turn_window(conv))` —
        # windowed to the capture cap and scrubbed of secrets — and only THEN
        # framed. Framing the raw turn made every clipped or redacted turn a
        # TEXT MISMATCH, i.e. a permanent false refusal on exactly the sessions
        # whose briefs contain a connection URL (`docker://:falkordb@…` becomes
        # `docker://:[REDACTED:connection_url]@…` in the graph). Measured: 5
        # turns in 5 sessions of the reference box's local record.
        # Residual, stated not hidden: the redaction table is VERSION-BOUND
        # (#5394), so a record written by a client running an older table than
        # this tree can still mismatch — which refuses, and is correct.
        try:
            texts = _capture_turn_texts_with_redactions(
                _capture_turn_window(filed))[0]
        except Exception as exc:
            raise CensusError(
                f"the local record's session {sid} could not be rendered "
                f"through the writer's own stored-text definition "
                f"({type(exc).__name__}: {exc}) — an absence is NOT "
                "established") from exc
        for index, framed in enumerate(texts):
            key = f"{sid}_t{index}"
            if wanted is not None and key not in wanted:
                continue
            rows_by_key[key] = {
                "node_key": key,
                "content": framed,
                "speaker": _capture_turn_role_text(framed)[0],
                # Declared absent, never fabricated: see
                # _LOCAL_RECORD_DECLARED_ABSENT. `point_kind` is here because
                # the local record has no kind to corroborate — writing the
                # constant 'event' and then LISTING it as compared (review P2)
                # claimed a comparison the source never performed.
                "status": None,
                "created_at": None,
                "point_kind": None,
                "embedding": None,
                "is_episodic": True,
            }
    return {
        "rows_by_key": rows_by_key,
        "non_turn_shaped": 0,
        "leg": "local",
        "record_dir": str(root),
        "files": [str(p.relative_to(root)) for p in files],
        "declared_absent": list(_LOCAL_RECORD_DECLARED_ABSENT),
        # PLACED sessions (the ones the positional key space covers): a
        # duplicate is counted in `duplicate_window` instead, so the two never
        # double-count one line.
        "sessions": len(conversations),
        "lines_parsed": parsed,
        "torn_tail": torn_tail,
        # Corruption that was DROPPED (never partially recovered). Named so a
        # tolerance nobody can see never reads as a parity pass.
        "unparsable_lines": unparsable_lines,
        # Parsed-but-malformed lines DROPPED from the read (not an object, no
        # usable `session_id`, no `conversation`, or a non-list one). The filing
        # leg dead-letters all four, so none can hide a turn — but a tolerance
        # nobody can see reads as a parity pass, so every one is named.
        "malformed_lines": malformed_lines,
        "empty_days": empty_days,
        "duplicate_window": duplicate_window,
        "filed_window": filed_window,
        "skipped_files": sorted(skipped_files),
        "unreadable_paths": sorted(unreadable_paths),
    }


def census(graph: dict, record: dict, *, sample: int,
           graph_name: str | None = None) -> dict:
    """The comparison and the classification. Pure: no I/O, no verdict I/O.

    ``graph_name`` is the graph the GRAPH leg actually read (:func:`resolve_graph_name`)
    and it is emitted in the report as ``graph``. It is not decoration: the consumer
    (``tortoise/cache_eviction.load_parity_report``) refuses a report that names no
    graph, because a loader that fills the name in with the CALLER's own has nothing
    left to compare — the binding then proves only that the operator typed one string
    (review P1 on PR #11004). The identity is therefore authored HERE, by the leg that
    opened the graph, and never by the consumer.
    """
    nodes, rows = graph["nodes"], record["rows_by_key"]
    # The record's own declaration of what it cannot compare. It narrows the
    # comparison, so it is validated HERE and surfaces in the report — a
    # narrower comparison that looked exactly like the strong one would be the
    # vacuous-certification shape this gate exists to refuse.
    declared_absent = set(record.get("declared_absent") or ())
    undeclinable = sorted(declared_absent & _UNDECLINABLE_FIELDS)
    if undeclinable:
        raise CensusError(
            f"the record declared {', '.join(undeclinable)} ABSENT, and "
            f"{', '.join(undeclinable)} IS the transcript — a source that "
            "cannot compare it cannot certify this purge")
    invented = sorted(declared_absent - _DECLARABLE_FIELDS)
    if invented:
        raise CensusError(
            f"the record declared {', '.join(invented)} ABSENT, which is not a "
            "field this census compares — an artifact must not carry a "
            "narrowing claim about a field that was never on the table")
    # ⛔ #8488 scope 1 (review cycle 3, P1): when context enrichment is ON the
    # node's vector describes `t.embeddingText` — an LLM rewrite of a neighbour
    # window that is NOT reconstructible from the conversation, and that NEITHER
    # record leg carries (`public.point` has no such column; the local JSONL is
    # `{session_id, conversation, ...}`). Comparing `content`/`speaker` and
    # waiving `embedding` would then report CLEAN over a node whose enriched
    # text and vector the purge would destroy — the false PASS this gate exists
    # to refuse. Fail closed, once, by name.
    # ⚠️ The refusal is over EVERY episodic node, not only the in-population
    # ones: an anomaly is still in the delete set (`is_episodic = true`), so an
    # enriched anomaly is exposed to the same loss — but it is LABELLED as an
    # episodic node, because calling it an "in-population turn" would send the
    # operator at the enrichment instead of at the anomaly the same run reports.
    enriched = sorted(tid for tid, node in nodes.items()
                      if node.get("embeddingText"))
    if enriched:
        # This census does NOT read `embeddingText` on either side. The hosted
        # record CAN hold it (`record_read.read_record_prior_turns` reads
        # `props->>'embeddingText'`, record_read.py:773-781) — the local JSONL
        # cannot — and no leg compares it, so this is a GAP in the comparison,
        # not an impossibility. Stated as such: a lane that reads this refusal
        # must be able to fix it (add the column to `_RECORD_TURNS_COLUMNS`,
        # select `t.embeddingText`, compare) instead of believing the record
        # cannot carry it.
        raise CensusError(
            f"{len(enriched)} episodic node(s) carry an `embeddingText` "
            f"(context enrichment, #8488; first: {enriched[0]}). This census "
            "does not compare that field on EITHER side — the hosted record can "
            "hold it (`props->>'embeddingText'`), the local JSONL cannot — so a "
            "purge would destroy text nothing here has verified a copy of. The "
            "comparison must be extended before this gate may open: an absence "
            "is NOT established")
    # ⛔ A SKIPPED leg compared NOTHING and must say so (review P3): the field
    # list is derived from `declared_absent`, so a `local-skipped` dict carrying
    # none reported all seven fields compared on a leg that was never opened —
    # the same artifact-honesty failure as naming a skipped leg 'hosted'.
    skipped = bool(record.get("skipped"))
    compared_fields = [] if skipped else (["key"] + [
        rcol for _, rcol, _ in _EXACT_FIELDS if rcol not in declared_absent])
    if not skipped and "embedding" not in declared_absent:
        compared_fields.append("embedding")
    unidentifiable = graph.get("unidentifiable") or []

    # The anomaly partition: turn-shaped nodes the deterministic id space
    # cannot place are NEVER purge candidates, and NEVER invisible — each is
    # reported under the class that excluded it.
    anomalies: dict[str, list[dict]] = {
        "non_decimal_suffix": [], "orphan": [], "cross_wired": [],
        "operator_flagged": [], "session_mismatch": [],
        "non_turn_point_kind": [], "non_string_id": [],
    }
    # A node the read could not key is still a node the purge would delete, so
    # it is REPORTED (under its own class) rather than vanishing between
    # `nodes` and `anomalies`. `read_graph_turns` carries them separately
    # because they have no `t.id` to be a dict key.
    for entry in graph.get("unidentifiable") or ():
        anomalies["non_string_id"].append(entry)
    for tid, node in sorted(nodes.items()):
        if node.get("pointKind") != "event":
            # IN THE DELETE SET (`is_episodic = true`, #10017's predicate) but
            # NOT a turn: `graph-scripts/backfill_is_episodic.py` stamps the
            # flag on regex-extracted points too, and `create_point`/
            # `update_point` accept it on any kind. Nothing compared it, so a
            # sweep driven by the recorded predicate would delete it on this
            # gate's word — its own anomaly class, by id, refusing the census.
            anomalies["non_turn_point_kind"].append(
                {"id": tid, "pointKind": node.get("pointKind")})
            continue
        if node.get("is_operator"):
            anomalies["operator_flagged"].append(
                {"id": tid, "is_operator": node["is_operator"]})
        derived = _TURN_ID_RE.match(tid)
        if derived is None:
            anomalies["non_decimal_suffix"].append({"id": tid})
        sids = node["sessions"]
        if not sids:
            anomalies["orphan"].append({
                "id": tid,
                "derived_session": derived.group("sid") if derived else None,
            })
        elif len(sids) > 1:
            anomalies["cross_wired"].append(
                {"id": tid, "sessions": sorted(sids)})
        elif derived is not None and next(iter(sids)) != derived.group("sid"):
            # Wired to a Session the id does not name — the capture writer
            # never produces this (it MERGEs CONTAINS from the true session),
            # so an external writer or a rename did. Excluded from the purge
            # set and REPORTED, never silently dropped from the census.
            anomalies["session_mismatch"].append({
                "id": tid, "wired_session": sorted(sids)[0],
                "derived_session": derived.group("sid"),
            })

    sessions: dict[str, dict] = {}
    for tid, node in sorted(nodes.items()):
        sids = node["sessions"]
        # In-population: wired to exactly one Session whose id + '_t<digits>'
        # IS this node's id (the `_capture_turn_ids` shape rule, one session),
        # and NOT flagged `is_operator` (the docstring: "a turn node carrying
        # true is an anomaly, not a parity field" — counting it as purgeable
        # while the anomaly line says it is excluded is a self-contradiction
        # in one report).
        in_population = False
        if (len(sids) == 1 and not node.get("is_operator")
                and node.get("pointKind") == "event"):
            sid = next(iter(sids))
            m = _TURN_ID_RE.match(tid)
            in_population = m is not None and m.group("sid") == sid
        if in_population:
            sessions.setdefault(sid, {"nodes": {}})["nodes"][tid] = node

    # Grouped ONCE: the per-session record count used to re-scan every record
    # row per session (O(sessions x rows) regex matches after the whole tenant
    # was already materialised). Same numbers, no quadratic term.
    rows_by_session: dict[str, int] = {}
    for key in rows:
        match = _TURN_ID_RE.match(key)
        if match is not None:
            rows_by_session[match.group("sid")] = (
                rows_by_session.get(match.group("sid"), 0) + 1)

    clean: list[dict] = []
    failed: list[dict] = []
    # Sessions the RECORD holds two or more windows for. Only the ones the graph
    # actually holds turn nodes for BLOCK: a duplicate nothing needs placed is
    # disclosed and non-blocking, because a refusal with no repair path is not a
    # guard (review P2).
    held_duplicates = {d["session"]: d
                       for d in (record.get("duplicate_window") or [])}
    for sid in sorted(sessions):
        if sid in held_duplicates:
            # In the graph's population but unplaceable in the record: name the
            # DUPLICATE as the cause. Falling through would report every turn
            # `missing_in_record`, i.e. point the operator at a graph/backfill
            # that would write the wrong window into the record.
            failed.append({
                "session": sid,
                "graph_turn_nodes": len(sessions[sid]["nodes"]),
                "record_turn_rows": 0,
                "counts_equal": False,
                "problems": [{
                    # ⛔ NO SYNTHETIC NODE ID (review P2). `f"{sid}_t*"` is not
                    # a turn id, and every consumer treats `node` as one —
                    # `purge_legacy_turns.classify_sessions` collected it into
                    # `sample_ids`, so a report could name a node it can never
                    # resolve. The held-back nodes are the real ids the graph
                    # holds for this session, and they are ALREADY in
                    # `graph_turn_nodes`; `node` stays None and consumers skip
                    # it.
                    "node": None, "field": "key",
                    "problem": "duplicate_window",
                    "detail": (
                        "the record holds "
                        f"{held_duplicates[sid]['windows']} windows for this "
                        "session — entry order is the only index it carries, "
                        "so a positional key is a guess. This is a RECORD-side "
                        "duplicate (a re-capture/resume), NOT a graph/backfill "
                        "gap: do not backfill over it"),
                }],
            })
            continue
        problems: list[dict] = []
        for tid, node in sorted(sessions[sid]["nodes"].items()):
            rec = rows.get(tid)
            if rec is None:
                problems.append({
                    "node": tid, "field": "key",
                    "problem": "missing_in_record",
                    "graph": {"id": tid},
                    "record": None,
                })
                continue
            for gprop, rcol, human in _EXACT_FIELDS:
                if rcol in declared_absent:
                    continue
                gval, rval = node.get(gprop), rec.get(rcol)
                if gval is None and rval is None:
                    continue
                if gval != rval:
                    problems.append({
                        "node": tid, "field": human,
                        "problem": "field_mismatch",
                        "graph": gval, "record": rval,
                    })
            ok, why = (True, "") if "embedding" in declared_absent else \
                _embedding_parity(node.get("embedding"), rec.get("embedding"))
            if not ok:
                problems.append({
                    "node": tid, "field": "embedding",
                    "problem": "field_mismatch", "detail": why,
                })
        record_rows_for_session = rows_by_session.get(sid, 0)
        graph_count = len(sessions[sid]["nodes"])
        record_count = record_rows_for_session
        verdict = {"session": sid, "graph_turn_nodes": graph_count,
                   "record_turn_rows": record_count,
                   "counts_equal": graph_count == record_count}
        if problems:
            verdict["problems"] = problems
            failed.append(verdict)
        else:
            clean.append(verdict)

    # `would_delete_*` is the PURGE SET among CLEAN sessions — what the gate
    # would actually permit. `held_back_*` is the mirror: turn nodes in a
    # REFUSED session, which are in the shape-derived purge population but are
    # NOT eligible (nothing compared them), so a REFUSED run cannot read as
    # "there is nothing to delete".
    would_delete = sum(v["graph_turn_nodes"] for v in clean)
    held_back = sum(v["graph_turn_nodes"] for v in failed)
    sample_out = []
    for v in clean:
        sids_nodes = sorted(sessions[v["session"]]["nodes"])
        sample_out.append({
            "session": v["session"], "count": v["graph_turn_nodes"],
            "sample": [
                {"id": tid,
                 "speaker": sessions[v["session"]]["nodes"][tid].get("speaker"),
                 "createdAt": sessions[v["session"]]["nodes"][tid].get("createdAt"),
                 # A 60-CHARACTER HEAD of the transcript, deliberately — the
                 # operator's eyeball comparison for a sampled turn. It is not
                 # "an identifying field" and this artifact is not
                 # content-free: the FAILURE evidence above (`graph`/`record`
                 # values) carries the full text of a mismatched turn, which a
                 # parity gate cannot do its job without. The whole transcript
                 # is never emitted for any turn.
                 "content_head": (str(
                     sessions[v["session"]]["nodes"][tid].get("content")
                     or "")[:60])}
                for tid in sids_nodes[:sample]],
        })

    anomaly_ids = sorted({a["id"] for entries in anomalies.values()
                          for a in entries})
    return {
        # ⚠️ REQUIRED by the consumer (`load_parity_report` refuses a nameless report):
        # emitting it here is what keeps the honest path open without making provenance
        # a caller's assertion.
        "instrument": INSTRUMENT,
        # ⚠️ REQUIRED by the consumer for the same reason, one level up: a report with
        # no graph identity was ATTRIBUTED to whatever graph the consumer asked about,
        # so `mismatched`/`unbound` could never fire on the only path that produces a
        # real report — a CLEAN census of another instance licensed a sweep of this one
        # (review P1 on PR #11004). ``None`` is emitted as ``None`` rather than omitted:
        # an artifact that does not know its target must say so, not look like a shape
        # from before the field existed.
        "graph": graph_name,
        "compared_fields": compared_fields,
        "declared_absent": sorted(declared_absent),
        "population": {
            # The read population is the DELETE SET (`is_episodic = true`).
            # `turn_shaped_nodes` is the TURN SHAPE inside it — NOT the purge
            # set: a turn-shaped node can still be excluded by the anomaly
            # partition (orphan, cross_wired, session_mismatch,
            # operator_flagged), and the suite pins that divergence. The purge
            # set is `dry_run.would_delete_turn_nodes` plus
            # `dry_run.held_back_turn_nodes`.
            # The DELETE SET, so it counts every node the read returned —
            # including the ones with no usable key, which carry no `t.id` to
            # be a dict key. Counting `nodes` alone understated the population
            # the label names (an unkeyable node is still in the sweep).
            "episodic_graph_nodes": len(nodes) + len(unidentifiable),
            "turn_shaped_nodes": sum(1 for n in nodes.values()
                                     if n.get("pointKind") == "event"),
            "sessions_with_graph_turns": len(sessions),
            "outside_population": len(anomaly_ids),
        },
        "record": {"turn_rows_total": len(rows),
                   "episodic_rows_not_turn_shaped":
                       record["non_turn_shaped"],
                   # WHICH authority this verdict came from: a hosted run and a
                   # local run must not be indistinguishable in the artifact
                   # (review P2) — the whole point of the finding is that the
                   # two are not the same store.
                   **({"leg": "local", "dir": record.get("record_dir"),
                       "files": record.get("files"),
                       "sessions": record.get("sessions"),
                       "lines_parsed": record.get("lines_parsed"),
                       "torn_tail": record.get("torn_tail") or [],
                       # Corruption dropped from the read, named: the surface
                       # the verdict rests on is stated, not implied.
                       "unparsable_lines":
                           record.get("unparsable_lines") or [],
                       # Parsed-but-malformed lines dropped from the read. A
                       # separate list from `unparsable_lines` on purpose: one
                       # is a parse failure, the other a shape the filing leg
                       # dead-letters. Both DROP, neither recovers.
                       "malformed_lines":
                           record.get("malformed_lines") or [],
                       # Tolerated AND counted: an empty day (sealed or
                       # plain). Never a silent skip (review P1).
                       "empty_days": record.get("empty_days") or [],
                       # Displaced by a same-day/same-shape collision: the
                       # surface is narrower than `files` alone suggests, so
                       # the omission is named rather than implied.
                       "skipped_files": record.get("skipped_files") or [],
                       # Record-SHAPED paths that exist and were NOT read (a
                       # different cause from a displaced day+shape collision,
                       # so its own key and its own stdout sentence).
                       "unreadable_paths": record.get("unreadable_paths") or [],
                       # Held back from the positional key space, with the graph
                       # population each one needs placed: a duplicate that no
                       # graph turn needs is disclosed and does not block.
                       "duplicate_window": [
                           dict(d, graph_turn_nodes=len(
                               sessions[d["session"]]["nodes"])
                               if d["session"] in sessions else 0)
                           for d in (record.get("duplicate_window") or [])],
                       # Which keys came from a WINDOWED list (the filing leg
                       # drops blank turns and keeps the last
                       # MAX_SESSION_TURNS), so an artifact never claims the
                       # file's raw positions.
                       "filed_window": record.get("filed_window") or []}
                      if record.get("leg") == "local" else
                      # Verbatim, never collapsed: a SKIPPED leg named
                      # 'hosted' is an artifact claiming a read that never
                      # happened (review cycle 6, P2).
                      {"leg": record.get("leg") or "hosted",
                       **({"skipped": True} if skipped else {})})},
        "parity": {
            "clean_sessions": clean,
            "failed_sessions": failed,
        },
        "anomalies": anomalies,
        "dry_run": {
            "would_delete_turn_nodes": would_delete,
            "held_back_turn_nodes": held_back,
            "blocked_sessions": [v["session"] for v in failed],
            "per_session_sample": sample_out,
            "note": "READ-ONLY dry run: this tool never deletes. The purge is "
                    "a separate, explicitly-gated operation (#10017 ordering).",
            "would_delete_ids": [tid for v in clean
                                 for tid in sorted(
                                     sessions[v["session"]]["nodes"])],
        },
        # CLEAN = measured the WHOLE population and it all matched. An anomaly
        # is a turn-shaped node the census could not place, so a shape-based
        # sweep would delete a node nothing was compared against: that is a
        # refusal (exit 2), not a footnote on a pass.
        "verdict": ("CLEAN" if not failed and not anomaly_ids else "REFUSED"),
        # Named per cause: a duplicate-window refusal is a RECORD-side state with
        # its own repair (a re-capture/resume), so folding it into "parity"
        # would send the operator at a graph/backfill that cannot fix it.
        "refused_for": (
            (["parity"] if any(_problem_is_not(v, "duplicate_window")
                                for v in failed) else []) +
            (["duplicate_window"] if any(_problem_is(v, "duplicate_window")
                                          for v in failed) else []) +
            (["outside_population"] if anomaly_ids else [])),
    }


def _problem_is(verdict: dict, name: str) -> bool:
    """Does this failed session's FIRST problem carry ``name``?"""
    problems = verdict.get("problems") or []
    return bool(problems) and problems[0].get("problem") == name


def _problem_is_not(verdict: dict, name: str) -> bool:
    """Is this failed session's FIRST problem something other than ``name``?

    The check is on ``problems[0]`` only — a session whose FIRST problem is
    ``name`` answers False even when a later problem differs, so a reader must
    not assume a scan of the whole list (review wording nit, no behaviour
    change).
    """
    problems = verdict.get("problems") or []
    return bool(problems) and problems[0].get("problem") != name


class _UsageParser(argparse.ArgumentParser):
    """Usage errors exit ``1`` — ``2`` is reserved for a REFUSED census.

    argparse's default is ``2``, which would make "you forgot --record-dsn"
    indistinguishable from "the record is missing turns" to any caller keying
    on the exit code (the same rule ``tools/graph_census.py`` states).
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _UsageParser(
        prog="turn_parity_census",
        description="The #10017 purge gate: exact per-session parity census, "
                    "graph turn nodes vs record turn rows. Read-only.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    # ⚠️ The presence check lists the store, so the credential has to be allowed
    # to run GRAPH.LIST — and that is an individually-granted command a
    # per-graph tenant user deliberately does not hold (#2113/#8617). The help
    # is where an operator reads the requirement BEFORE choosing the flag, so
    # the requirement is stated here rather than discovered from a refusal.
    src.add_argument("--uri", help="FalkorDB URI of the target graph; the graph "
                                   "leg proves the graph already exists with "
                                   "GRAPH.LIST, so this credential must be "
                                   "allowed to run it AND the handshake's INFO "
                                   "(a per-graph tenant user is allowed "
                                   "neither, by design)")
    src.add_argument("--db-path", help="embedded graph path instead of a URI")
    # ⛔ THE HELP MUST NOT CLAIM A SCOPE THE CODE DOES NOT HAVE (review P3).
    # This read "applies to BOTH legs" while `--graph-name` is passed to
    # `read_graph_turns` only — the record tenant is `--org-id`/`--graph-id` —
    # so it contradicted the module contract at the same head (:354-360). A
    # help string is the only documentation an operator reads at the moment of
    # choosing the flag, so the stale half is corrected here rather than left
    # for the next reader to disambiguate.
    parser.add_argument("--graph-name", default=None,
                        help="the graph inside the store (default: the URI's, "
                             "or 'tortoise' for --db-path); selects the GRAPH "
                             "leg only — it never scopes the record read (the "
                             "record's tenant is --org-id/--graph-id)")
    parser.add_argument("--record-dsn", default=None,
                        help="hosted record DSN (default: TORTOISE_RECORD_DSN)")
    parser.add_argument("--local-record-dir", default=None,
                        help="the LOCAL record leg instead of the hosted one: "
                             "the day-addressed capture-log directory (e.g. "
                             "~/.tortoise/session-events). This is the store "
                             "the owner named as the authority while hosted "
                             "setup is postponed (2026-11-01 ruling, #10017)")
    parser.add_argument("--org-id", default=os.environ.get(
        "TORTOISE_RECORD_ORG_ID", ""), help="record tenant org")
    parser.add_argument("--graph-id", default=os.environ.get(
        "TORTOISE_RECORD_GRAPH_ID", ""), help="record tenant graph")
    parser.add_argument("--json", default=None,
                        help="write the full report to this path")
    parser.add_argument("--sample", type=int, default=5,
                        help="sample size per session in the dry-run report")
    parser.add_argument("--expect-empty", action="store_true",
                        help="allow a graph holding ZERO is_episodic nodes to "
                             "certify; without it that is exit 3. Presence "
                             "refuses a name the store does not hold; a "
                             "name it DOES hold but that was not the "
                             "intended graph still certifies under this "
                             "flag, so pass it only for a graph known to "
                             "have been drained")
    args = parser.parse_args(argv)

    if args.sample < 0:
        parser.error("--sample must be >= 0")

    # ⛔ THE GRAPH TARGET GETS THE SAME PRESENCE-NOT-TRUTHINESS RULE the record flags
    # already carry below (review P2 on #11097). `--uri "$TORTOISE_DB_URI"` with the
    # variable UNSET is a set-but-empty value; `read_graph_turns` tests truthiness, so
    # the run silently fell through to the EMBEDDED store — and because this tool now
    # authors the graph identity into the artifact, that accident produced a report
    # naming `tortoise`, which the cache census accepts for a hosted graph of the same
    # name. A shell accident therefore licensed a sweep of an instance the census never
    # touched: the exact `different instance` shape the binding exists to refuse.
    # `--graph-name` is in the same rule for the mirror reason (review P2, cycle 2):
    # `--graph-name ""` used to win `graph_name is not None` and be written into the
    # artifact as `graph: ""` while the EMBEDDED read fell back to `tortoise` — an
    # artifact that misstates its own target AND can never be loaded as evidence.
    # The rule itself is `empty_graph_target_flag` — ONE implementation, because
    # `purge_legacy_turns` opens a graph from the same three flags and was exposed to
    # the same accident (review P2, cycle 3).
    empty_flag = empty_graph_target_flag(args)
    if empty_flag:
        sys.stderr.write(f"turn_parity_census: {empty_flag}\n")
        return 1

    # ⛔ THE FLAG'S PRESENCE, NOT ITS TRUTHINESS (review P2). `if
    # args.local_record_dir:` treated a set-but-EMPTY value as "no flag", so
    # `--local-record-dir "$EVENTS_DIR"` with an unset variable fell into the
    # hosted branch and measured an ambient `TORTOISE_RECORD_DSN` — the store
    # the 2026-11-01 ruling says is NOT the authority — and could exit 0 CLEAN
    # over it. That is precisely the silent authority switch the flag's own
    # contract forbids. An empty value is neither leg: it refuses by name.
    local_leg = args.local_record_dir is not None
    if local_leg and not args.local_record_dir.strip():
        sys.stderr.write(
            "turn_parity_census: --local-record-dir was passed but is EMPTY "
            "(a shell expansion like --local-record-dir \"$UNSET_VAR\" "
            "yields nothing). An empty flag value is NOT 'no flag': falling "
            "through would let an ambient TORTOISE_RECORD_DSN silently move "
            "the census onto the hosted store the 2026-11-01 ruling says is "
            "NOT the authority, so this refuses instead of reading either "
            "store. The record cannot be read, so an absence is NOT "
            "established.\n")
        return 1

    if local_leg:
        # The local leg is tenant-less, so an AMBIENT `TORTOISE_RECORD_DSN`
        # must not reach it (review P1): only an explicitly passed
        # `--record-dsn` is an ambiguity worth refusing. Refusing on the
        # ambient variable made the documented flag unusable on any box that
        # exports the DSN the repo's own `.env.example` sets.
        # ⛔ PRESENCE, NOT TRUTHINESS (review P3) — the same rule the hosted arm
        # below applies to the same flag. `--record-dsn ""` is a SET flag whose
        # value is empty (a shell expansion of an unset variable), not "no
        # flag": a truthiness test let `--local-record-dir DIR --record-dsn ""`
        # through, so the documented "an explicit flag combination is refused"
        # guard did not exist for the one spelling an unset variable produces.
        if args.record_dsn is not None:
            sys.stderr.write(
                "turn_parity_census: --local-record-dir and --record-dsn are "
                "mutually exclusive — the census compares against ONE record, "
                "and silently preferring one would measure the wrong "
                "authority.\n")
            return 1
        record_dsn = ""
    else:
        # ⛔ THE EXACT SAME RULE ON THE HOSTED SIDE (review P1) — it was applied
        # to `--local-record-dir` above and NOT here, so `--record-dsn ""` (a
        # shell expansion of an unset variable) fell through to an ambient
        # `TORTOISE_RECORD_DSN` and measured a store the operator never named,
        # under their own tenant — the drill/scratch-DSN class that reads as a
        # CLEAN verdict about production. Presence of the flag selects exactly
        # its value; an empty value is "no record leg named", never a fallback.
        if args.record_dsn is not None:
            if not args.record_dsn.strip():
                sys.stderr.write(
                    "turn_parity_census: --record-dsn was passed but is EMPTY "
                    "(a shell expansion like --record-dsn \"$UNSET_VAR\" "
                    "yields nothing). An empty flag value is NOT 'no flag': "
                    "falling through would silently measure an ambient "
                    "TORTOISE_RECORD_DSN the operator did not name, so this "
                    "refuses instead of reading either store. The record "
                    "cannot be read, so an absence is NOT established.\n")
                return 1
            record_dsn = args.record_dsn
        else:
            record_dsn = os.environ.get("TORTOISE_RECORD_DSN", "")
        if not record_dsn:
            sys.stderr.write(
                "turn_parity_census: no record leg — pass --record-dsn / set "
                "TORTOISE_RECORD_DSN for the hosted record, or "
                "--local-record-dir for the local one. The record cannot be "
                "read, so an absence is NOT established.\n")
            return 1
        # The tenant is the HOSTED leg's requirement only: the local record is
        # keyed by session id and carries no tenant at all.
        if not args.org_id or not args.graph_id:
            sys.stderr.write(
                "turn_parity_census: --org-id/--graph-id (or "
                "TORTOISE_RECORD_ORG_ID/TORTOISE_RECORD_GRAPH_ID) are "
                "required — the census must be filed under the deployment's "
                "real tenant, never a guess.\n")
            return 1

    try:
        measured_graph = resolve_graph_name(args.uri, args.db_path,
                                            args.graph_name)
        graph = read_graph_turns(args.uri, args.db_path, measured_graph)
    except CensusError as exc:
        sys.stderr.write(f"turn_parity_census: {exc}\n")
        return 3
    except ValueError as exc:
        # The canonical URI → endpoint derivation refuses a scheme it does not know.
        # Same outcome as an unreachable graph (exit 3), never a traceback: the target
        # was never established, so nothing may be certified about it.
        sys.stderr.write(f"turn_parity_census: the graph could not be resolved "
                         f"from {args.uri!r} ({exc}) — an absence is NOT "
                         f"established\n")
        return 3

    # Zero episodic nodes is not evidence of parity — it is also the answer a
    # wrong --graph-name gives when the store HAPPENS to hold a graph of that
    # name, or when a wrong --db-path points at another store that does. (A
    # --db-path typo that opens a FRESH store does NOT reach this branch: the
    # store lists nothing, so the presence check above refuses it first — which
    # is why the presence check, not this branch, is what closed the create-a-
    # graph defect #11112.) So by the time this branch is reached the named graph
    # is KNOWN to EXIST, but not known to be the one the operator meant. The
    # record axis refuses a wrong tenant loudly; the graph axis can only refuse
    # an absent name, and this branch is exit 3 unless the operator says the
    # draining is expected (#10017's post-purge re-run) — an affirmative claim
    # about THIS graph, which is why --expect-empty is not a way to silence a
    # suspicious run.
    # ... and `unidentifiable` is part of that population: a graph whose
    # episodic nodes ALL came back without a usable `t.id` is not an EMPTY
    # graph, and reporting it as "ZERO is_episodic nodes" would be factually
    # wrong about the target. Those nodes are `non_string_id` anomalies, which
    # REFUSE the census below (exit 2) — the accurate verdict, and a message
    # the operator can act on.
    graph_is_empty = (not graph["nodes"] and not graph.get("unidentifiable"))
    if graph_is_empty and not args.expect_empty:
        sys.stderr.write(
            "turn_parity_census: the graph holds ZERO is_episodic nodes — an "
            "absence is NOT established. The graph DOES exist (presence was "
            "checked first); it is its emptiness that is unproven, so pass "
            "--expect-empty if the graph is known to hold no turn nodes.\n")
        return 3

    # ⛔ THE RECORD LEG IS OPENED ONLY WHEN THERE IS SOMETHING TO COMPARE
    # AGAINST IT (review cycle 5, P2). With `--expect-empty` and a graph that
    # really holds zero episodic nodes, the record has nothing to be in parity
    # WITH — and a box that never captured locally has no capture log at all, so
    # reading it first made the flag unreachable on the leg this tool adds
    # (while the same input certified on the hosted leg, which tolerates zero
    # rows). The empty leg is LABELLED `local-skipped`/`hosted-skipped` so the
    # artifact cannot read as a real read.
    if graph_is_empty and local_leg:
        # LOCAL ONLY: the hosted read tolerates zero rows by itself, and
        # skipping IT reported a read of a DSN that was never opened (review
        # cycle 6, P1 — an unreachable hosted DSN certified exit 0).
        #
        # ⛔ And the skipped leg carries the leg's own DECLARED-ABSENT set plus
        # `skipped` (review P3): a bare dict made `compared_fields` report all
        # seven fields compared on a leg that was never opened — the same
        # artifact-honesty failure this branch's sibling polices.
        record = {"rows_by_key": {}, "non_turn_shaped": 0,
                  "leg": "local-skipped", "skipped": True,
                  "record_dir": args.local_record_dir,
                  "declared_absent": list(_LOCAL_RECORD_DECLARED_ABSENT)}
    else:
        try:
            if local_leg:
                record = read_local_record_turns(args.local_record_dir)
            else:
                record = read_record_turns(record_dsn, args.org_id,
                                           args.graph_id)
        except CensusError as exc:
            sys.stderr.write(f"turn_parity_census: {exc}\n")
            return 3

    try:
        report = census(graph, record, sample=args.sample,
                        graph_name=measured_graph)
    except CensusError as exc:
        sys.stderr.write(f"turn_parity_census: {exc}\n")
        return 3
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")

    pop = report["population"]
    # The target is PRINTED as well as emitted: an operator reading the run must be
    # able to see which graph produced it, and that is the same fact the artifact
    # carries for the consumer.
    print(f"graph      : {report['graph']}")
    print(f"population : {pop['episodic_graph_nodes']} episodic graph node(s) "
          f"(the delete set), {pop['turn_shaped_nodes']} of them turn-shaped, "
          f"across {pop['sessions_with_graph_turns']} session(s)")
    if report["record"].get("leg", "").endswith("-skipped"):
        print("record     : SKIPPED — the graph holds ZERO episodic nodes, so "
              "there is nothing to compare a record against (NOT a measured "
              "zero)")
    else:
        print(f"record     : {report['record']['turn_rows_total']} turn rows "
              f"({report['record']['episodic_rows_not_turn_shaped']} episodic "
              f"rows not turn-shaped)")
    if report["record"].get("skipped"):
        # Review P3: the artifact must not state a comparison a skipped leg never
        # performed — say NOTHING was compared, by name.
        print("NOT COMPARED: NOTHING — the record leg was SKIPPED and never "
              "opened, so no field was compared (not even the key)")
    elif report.get("declared_absent"):
        # A narrower comparison must never read as the strong one.
        pronoun = "it" if len(report["declared_absent"]) == 1 else "them"
        print(f"NOT COMPARED: {', '.join(report['declared_absent'])} "
              f"(the record does not carry {pronoun}; compared: "
              f"{', '.join(report['compared_fields'])})")
    for filed in report["record"].get("filed_window") or []:
        # Disclosed, never silent: these keys are the FILED list's positions, not
        # the file's, so the artifact must say the list was transformed.
        print(f"filed window: session {filed['session']} — the filing leg "
              f"POSTed {filed['filed_turns']} of the record's "
              f"{filed['record_turns']} turn(s), so its keys are the FILED "
              f"list's positions (NOT the file's raw positions)")
    for dup in report["record"].get("duplicate_window") or []:
        held = ("REMOVED FROM THE COMPARISON — the graph holds its turns and "
                "no window can be placed" if dup.get("graph_turn_nodes")
                else "disclosed only — no graph turn node needs it placed")
        print(f"duplicate  : session {dup['session']} holds {dup['windows']} "
              f"windows — {held}")
    if record.get("torn_tail"):
        for torn in record["torn_tail"]:
            print(f"torn tail  : {torn['at']} (mid-write, tolerated, "
                  f"counted — NOT a parity pass)")
    for bad in report["record"].get("unparsable_lines") or []:
        # Disclosed, never silent, and never recovered: the line was dropped,
        # so any graph turn it may have described is `missing_in_record`.
        print(f"unparsable : {bad['at']} ({bad['error']}) — the line was "
              f"DROPPED, not recovered; any turn it described is "
              f"missing_in_record, so this is NOT a parity pass")
    for bad in report["record"].get("malformed_lines") or []:
        # Parsed but malformed: dropped for the same reason, and dead-lettered
        # by the filing leg, so it filed no turn and can hide none.
        print(f"malformed  : {bad['at']} ({bad['error']}) — the line was "
              f"DROPPED, not recovered; any turn it may have described is "
              f"missing_in_record, so this is NOT a parity pass")
    for empty_day in report["record"].get("empty_days") or []:
        # Disclosed, never silent: a complete verified archive of no records
        # cannot hide a turn, but a tolerance nobody can see reads like a day
        # that went missing. The PROVENANCE is stated per file: "provably"
        # belongs to the sealed arm (the seal round-trips the archive before it
        # unlinks the plain source). An empty PLAIN day is a different fact — a
        # 0-byte file that never held a record — and calling it "sealed …
        # provably" would be false on both halves (review P2).
        if empty_day.endswith(".gz"):
            why = "a sealed day that provably held no records"
        else:
            why = ("an empty plain day — never held a record (the writer "
                   "creates and writes in one call)")
        print(f"empty day  : {empty_day} ({why} — tolerated and counted, "
              f"NOT a parity pass)")
    for skipped in report["record"].get("skipped_files") or []:
        # Named, not implied: one queue key per day+shape means a plain file
        # under `archive/` cannot be read alongside the root's own file of the
        # same day. Fail-closed on the verdict; disclosed on the surface.
        print(f"skipped    : {skipped} (same day+shape as a file the root "
              f"store already supplies — displaced, NOT read; a session "
              f"living only here reads as missing_in_record)")
    for unreadable in report["record"].get("unreadable_paths") or []:
        # Its OWN sentence: this class was never read for a DIFFERENT reason, and
        # the collision cause above named the wrong repair for it (review P3).
        print(f"unreadable : {unreadable} — present and NOT read; a session "
              f"living only here reads as missing_in_record")
    for cls, entries in report["anomalies"].items():
        if entries:
            print(f"anomaly    : {len(entries)} {cls} "
                  f"(OUTSIDE the compared population — reported, never "
                  f"deleted by this tool)")
            for entry in entries[:5]:
                print(f"  OUTSIDE  {cls}: {entry['id']}")
    print(f"parity     : {len(report['parity']['clean_sessions'])} session(s) "
          f"CLEAN, {len(report['parity']['failed_sessions'])} FAILED")
    for v in report["parity"]["failed_sessions"]:
        head = ", ".join(f"{p['node']}:{p['field']}({p['problem']})"
                         for p in v["problems"][:3])
        more = "" if len(v["problems"]) <= 3 else f" +{len(v['problems'])-3} more"
        print(f"  REFUSED  session {v['session']}: {head}{more}")
    print(f"dry run    : would delete {report['dry_run']['would_delete_turn_nodes']} "
          f"turn node(s), in {len(report['parity']['clean_sessions'])} CLEAN "
          f"session(s) (READ-ONLY — this tool never deletes)")
    if report["dry_run"]["held_back_turn_nodes"]:
        print(f"held back  : {report['dry_run']['held_back_turn_nodes']} turn "
              f"node(s) in {len(report['parity']['failed_sessions'])} REFUSED "
              f"session(s) are in the delete set but NOT eligible — nothing "
              f"compared them")
    if report.get("refused_for"):
        print(f"refused    : {', '.join(report['refused_for'])}")
    print(f"verdict    : {report['verdict']}")

    return 0 if report["verdict"] == "CLEAN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
