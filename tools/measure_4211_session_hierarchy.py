#!/usr/bin/env python3
"""#4211 — is there a session-hierarchy structure for the candidate-expansion arm to walk?

The gate (issue #4211, comment 2026-11-19) says: re-measure the edge population FIRST,
on the graph the feature would actually walk. The 2026-09-19 W6B census
(`docs/scoping/receipts/2026-09-19-w6b-graph-candidate-census.json`) reported
**0 `aboutObject` edges** on the capture-shaped store and treated that as the premise
failing.

Two things the census conflated, and this tool keeps apart:

  * the **hierarchy** the arm walks — ``(:Session)-[:CONTAINS]->(:turn Point)``;
  * **entity attachment** — ``(:Point|:Session|:Event)-[:aboutObject]->(:Object)``,
    which is what the gate names but NOT what the walker traverses.

ARMS
----
``store``       a READ-ONLY COPY of a real embedded store (default
                ``~/.tortoise/tortoise.db``). Copied with ``shutil.copy2`` before
                the engine opens it; the original is never opened by the engine.
``writer``      a FRESH capture driven by the REAL writer (``TortoiseSDK._capture_session``,
                the one live capture implementation; ``sdk.create_object`` mints the
                WorkItem Object the link pass resolves) on ``TORTOISE_DB_URI``.
                Three sessions: a realistic ref density (2 of 8 turns name an issue),
                no ref at all, and every turn naming it. Measures BEFORE and AFTER and
                reports the DELTA — a replay proves nothing about a writer, so a run
                that replays a known session reports ``unmeasurable`` instead of numbers.
``live``        the graph the caller names (default ``docker://:@localhost:16379/tortoise``,
                the URI string recorded at ``~/.zshrc:48``). ⛔ the tool does NOT read a
                shell profile: ``role_evidence.uri_source`` records what actually
                SUPPLIED the URI (``--live-uri``, or the built-in constant), so the
                receipt never asserts a provenance it did not measure. Read-only.
``instrument``  every graph in that container holding Sessions + episodic turns, aggregated.
                This is the REGIME THE CENSUS MEASURED, at scale. ⛔ Do NOT name one
                writer for it (#13919 round 8): the population is MIXED, and this arm
                MEASURES the mix — ``contains_targets_by_kind`` on the censused
                population is ``event`` 24,999 / ``session-transcript`` 12,606, and
                ``tortoise/retrieval.py`` records that ``session-transcript`` is
                written ONLY by the eval ingest lane and explicitly NOT by the
                ask-fixture seeder (``tools/ask_spotcheck.py::
                seed_capture_turn_store`` hardcodes ``"k": "event"``). Neither the
                seeder nor the eval ingest V1 leg (``ingest.py``, zero ``aboutObject``
                writes) runs the entity-link pass — but the V2 leg DOES (``#2165`` /
                E7-D7 in ``ingest_v2.py``), so 0 ``aboutObject`` over this population
                also measures the ABSENCE OF v2 EXTRACTION OUTPUT in these graphs, not
                a property of "the eval lane". That attribution is DERIVED from the
                breakdown the arm measured (never asserted) and is what its `note`
                publishes. The product's own capture path is what ``writer`` measures.
``corpus``      the capture journal ``~/.tortoise/session-events`` (no graph). Turns are
                counted BOTH ways — per-record occurrences AND distinct per session —
                because one session writes one record per end-of-session capture, each
                carrying the whole transcript, so occurrences over-count.

Usage
-----
    uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"     # never a bare pytest
    TORTOISE_DB_URI="docker://:@127.0.0.1:${TORTOISE_TEST_DOCKER_PORT}/m4211_hierarchy" \\
      uv run python tools/measure_4211_session_hierarchy.py \\
        --arms store,writer,live,instrument,corpus \\
        --out docs/scoping/receipts/2026-11-19-4211-hierarchy-remeasure.json

⚠️ Name ``--arms store,writer,live,instrument,corpus`` to reproduce the committed
receipt: the DEFAULT set is the READ-ONLY one (``writer`` MUTATES the graph
``TORTOISE_DB_URI`` names, and that variable is the product's connection string, not a
test-only one), so a bare run is a SHRUNKEN measurement — it records
``instrument.arms_not_requested`` rather than leaving the absence unexplained. The write
arm additionally REFUSES — rather than acting — when the target graph name does not look
ephemeral (``m4211``/``test``/``tmp``/``lane``/``scratch``/``e2e``/``ci_``);
``--allow-non-ephemeral-writes`` overrides that deliberately and is recorded in argv and
in ``arms.writer.non_ephemeral_write_override``.

Exit codes: 0 = MEASURED (whatever the numbers are), 2 = could not measure.

⛔ ``2`` is reached on TWO different paths and the reason lives in a different place on
each — a caller must not read one contract off the other (#13919 review round 7, item 4):

* **A run that got as far as the arms.** The reason is in the receipt's TOP-LEVEL
  ``unmeasurable`` block, written whenever ANY arm could not measure: a replayed writer,
  a graph that raised during the census, a census that inspected NO graph at all (an
  empty instance reaches a wrong port/host exactly like a real instance with no
  captures, so a zero shape is never a measurement), an unreadable node count, or a
  ``skipped`` arm (a store that is not on this box, a graph that is not in the file, no
  ``TORTOISE_DB_URI``, no capture journal) — a skip measured NOTHING, so it is a
  refusal, never a completed capture. A ``--out`` receipt is never replaced by an
  unmeasurable run (it writes ``<name>.unmeasurable.json`` beside it), so a re-run
  scripted as ``--out <receipt> || exit 1`` cannot overwrite the receipt it failed to
  reproduce.
* **A refusal taken BEFORE any arm runs** — an interpreter below 3.12, or an ``--arms``
  value that names no measurable arm. There is **no receipt at all**: the reason is on
  **stderr** (attributable stderr is the #5128 invariant) and ``--out`` is not written.
  A caller that reads the receipt for its reason will find no file — it must read stderr.

Definition discipline (#4211 review round 1): a *turn* is
``coalesce(p.is_episodic,false)=true AND p.pointKind='event'`` — the product's own
discriminator (``tortoise/sdk.py``). A bare ``is_episodic`` also matches extracted
episodic MEMORY points, which are not turns; both counts are reported so the difference
is visible instead of silent.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import would fail first, and an UNATTRIBUTED failure is the harm
# #5128 was filed for. Same shape as the sibling census (tools/edge_census.py,
# tools/backfill_graph_turns.py); the guard must be the first statement after the
# docstring and `import sys`, which tests/test_entry_point_python_guard.py pins for
# every tools/ entry point. MEASURED: the pre-guard tool redded that corpus pin for
# BOTH of its cases (parametrised over tools/*.py), which is what made #13919 a
# PR-introduced failure rather than a main baseline red.
#
# The refusal must not wear a VERDICT's status (#4053/#11342, the same reason
# `tools/ram_window_census.py` — the sibling census — prints its own message): this
# tool's DECLARED exit contract is 0 = MEASURED / 2 = could not measure, so
# `raise SystemExit("<message>")` would exit 1, a code this tool does not otherwise
# use and which reads to a caller as a verdict about the measurement. An old
# interpreter is not a verdict — it is "could not measure" — so it exits 2, and
# since `SystemExit(<int>)` carries no message the refusal is printed explicitly.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/measure_4211_session_hierarchy.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/measure_4211_session_hierarchy.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_TOOL_PATH = Path(__file__).resolve()

DEFAULT_STORE = "~/.tortoise/tortoise.db"
DEFAULT_STORE_GRAPH = "tortoise"
DEFAULT_LIVE_URI = "docker://:@localhost:16379/tortoise"
DEFAULT_SESSIONS_DIR = "~/.tortoise/session-events"

#: The default arm set is the READ-ONLY one. ``writer`` mutates the target, so it is
#: opt-in: a bare `python tools/measure_4211_session_hierarchy.py` must not be able to
#: write into whatever graph ``TORTOISE_DB_URI`` happens to name (#4211 review N8).
DEFAULT_ARMS = "store,live,instrument,corpus"

#: Every arm the tool knows — the read-only default set plus the opt-in write arm.
#: ⛔ Used to REFUSE a typo and to record what was NOT requested: an absent arm is not a
#: skipped arm, and the default set is read-only, so a receipt produced without
#: ``--arms store,writer,live,instrument,corpus`` is a SHRUNKEN measurement that must
#: say so in the artifact (the same discipline `graphs_without_turn_store` applies).
KNOWN_ARMS = (*DEFAULT_ARMS.split(","), "writer")

#: Graph names the writer arm may write into without an explicit override.
#:
#: ⛔ Matched as a DELIMITED SEGMENT, never as a substring (#13919 review F1). A
#: substring test reads "ephemeral" out of ordinary production names — `test` is a
#: substring of `latest` and of `contest`, so a substring `any(m in g ...)` silently
#: admitted `tortoise_latest`, `prod_latest` and `contest_graph`, and the writer arm
#: would then mint its three `m4211-*` fixture sessions, the shared claim Point and
#: the Object `test/repo#42` into a graph N8's refusal exists to protect. The
#: strings here are segmented names (the trailing `_` on `ci_` is dropped because the
#: delimiter already carries it); `_looks_ephemeral` splits the candidate on the same
#: delimiters, so `m4211_hierarchy` and `ci_x` still match while `latest` does not.
EPHEMERAL_GRAPH_MARKERS = ("m4211", "test", "tmp", "lane", "scratch", "e2e", "ci")

#: The product modules the measurement runs THROUGH: their digests are recorded beside
#: ``instrument.sha256`` so an SDK move is distinguishable from a graph move (#4211 N7).
MEASURED_THROUGH = ("tortoise/sdk.py", "tortoise/session_link.py")

#: The product's own turn discriminator (``is_episodic`` alone also matches extracted
#: episodic MEMORY points).
TURN = "coalesce(p.is_episodic,false)=true AND p.pointKind='event'"

# ── the Cypher, verbatim, so the receipt carries what produced it ─────────
QUERIES: list[tuple[str, str]] = [
    ("nodes_by_label",
     "MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS n "
     "ORDER BY n DESC"),
    ("edges_by_type",
     "MATCH ()-[r]->() RETURN type(r) AS edge, count(*) AS n ORDER BY n DESC"),
    ("aboutObject_by_endpoint",
     "MATCH (s)-[r:aboutObject]->(o) "
     "UNWIND labels(s) AS sl RETURN sl AS src_label, labels(o) AS dst_labels, "
     "count(*) AS n ORDER BY n DESC"),
    ("points_total", "MATCH (p:Point) RETURN count(p) AS n"),
    ("turns_total", f"MATCH (p:Point) WHERE {TURN} RETURN count(p) AS n"),
    ("episodic_points_any_kind",
     "MATCH (p:Point) WHERE coalesce(p.is_episodic, false) = true "
     "RETURN count(p) AS n"),
    ("turns_with_aboutObject",
     f"MATCH (p:Point)-[:aboutObject]->(:Object) WHERE {TURN} "
     "RETURN count(DISTINCT p) AS n"),
    ("nonepisodic_points_with_aboutObject",
     "MATCH (p:Point)-[:aboutObject]->(:Object) "
     "WHERE coalesce(p.is_episodic, false) = false "
     "RETURN count(DISTINCT p) AS n"),
    ("sessions_total", "MATCH (s:Session) RETURN count(s) AS n"),
    ("sessions_with_aboutObject",
     "MATCH (s:Session)-[:aboutObject]->(:Object) "
     "RETURN count(DISTINCT s) AS n"),
    # ── the hierarchy, measured three ways so no single reading can hide a defect ──
    ("contains_edges",
     "MATCH (:Session)-[r:CONTAINS]->(:Point) RETURN count(r) AS n"),
    ("contains_distinct_pairs",
     # ⛔ Node IDENTITY, never `count(DISTINCT s.id + '|' + p.id)`: a Point with no `id`
     # property concatenates to null, so `count(DISTINCT null)` answers 0 and the
     # multiplicity guard below then reports "0 distinct pairs, 0 duplicates" for a
     # graph that wired every edge — the guard silently disabled on exactly the
     # fixture-shaped graphs this arm measures. MEASURED on an embedded fixture (one
     # Session, one `{pointKind:'event'}` Point with no id, one duplicate CONTAINS):
     # 2 edges → id-concat 0, `WITH DISTINCT s, p` 1.
     "MATCH (s:Session)-[:CONTAINS]->(p:Point) WITH DISTINCT s, p "
     "RETURN count(*) AS n"),
    ("contains_duplicate_pairs",
     "MATCH (s:Session)-[r:CONTAINS]->(p:Point) "
     "WITH s, p, count(r) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("sessions_with_contains",
     "MATCH (s:Session)-[:CONTAINS]->(:Point) "
     "RETURN count(DISTINCT s) AS n"),
    # A session CONTAINS-wires MORE than its turns: the extraction lane wires claim
    # Points into the same session, and a chunk writer wires ``session-transcript``
    # Points (an ``is_episodic`` kind the TURN discriminator deliberately excludes).
    # Without this breakdown a turn count and a CONTAINS count look like they
    # disagree. ⛔ Do NOT name a writer for it here: the population this arm reads is
    # NOT one writer's output — ``tortoise/retrieval.py`` records that
    # ``session-transcript`` is written ONLY by the eval ingest lane, "⚠️ NOR the
    # ask-fixture seeder" (``tools/ask_spotcheck.py::seed_capture_turn_store``
    # hardcodes ``"k": "event"``). The breakdown is the measurement; the attribution
    # is what this arm refuses to assert (#13919 review round 7).
    ("contains_targets_by_kind",
     "MATCH (:Session)-[:CONTAINS]->(p:Point) "
     "RETURN p.pointKind AS pointKind, coalesce(p.is_episodic, false) AS episodic, "
     "count(*) AS n ORDER BY n DESC"),
    # ⛔ A key NAMED `*turn*` applies the turn discriminator, and the any-kind reading is
    # reported BESIDE it (``sessions_with_gt1_contains_point``) — the same discipline as
    # ``turns_total`` / ``episodic_points_any_kind`` above. Without the filter these keys
    # count a session's claim Points and its non-turn ``is_episodic`` chunks as turns, so
    # on a graph of one turn + one claim Point per session the gate's own falsifier
    # (``sessions_with_gt1_contains_turn ≈ 0``) could never fire.
    ("sessions_with_gt1_contains_turn",
     f"MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE {TURN} "
     "WITH s, count(DISTINCT p) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("sessions_with_gt1_contains_point",
     "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
     "WITH s, count(DISTINCT p) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("sessions_with_gt1_entity_linked_turn",
     f"MATCH (s:Session)-[:CONTAINS]->(p:Point)-[:aboutObject]->(:Object) WHERE {TURN} "
     "WITH s, count(DISTINCT p) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("sessions_with_gt1_aboutObject_edge",
     # ⛔ `count(DISTINCT o)` answers "wired to >1 OBJECT", not "carries >1 EDGE" —
     # a double-wired Session→Object collapsed to c=1 and the key read 0 beside the
     # write bug it is named after (#12244). The CONTAINS side got exactly this guard
     # (`contains_distinct_pairs` / `contains_duplicate_pairs`); the `aboutObject` side
     # had none, and this key is the one whose NAME would be read as providing it.
     # The matching any-kind reading is reported beside it (#13919 review round 9).
     "MATCH (s:Session)-[r:aboutObject]->(:Object) "
     "WITH s, count(r) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("sessions_with_gt1_aboutObject_object",
     "MATCH (s:Session)-[:aboutObject]->(o:Object) "
     "WITH s, count(DISTINCT o) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("aboutObject_duplicate_pairs",
     "MATCH (s:Session)-[r:aboutObject]->(o:Object) "
     "WITH s, o, count(r) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("turns_per_session_contains_max_avg",
     f"MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE {TURN} "
     "WITH s, count(DISTINCT p) AS c RETURN max(c) AS max_c, "
     "toFloat(sum(c)) / count(s) AS avg_c"),
    # Which WRITER generation wrote a Session: the current capture writes
    # is_episodic/capture_ok/capture_extractor; an older population carries only the
    # session-MERGE three. Reported so a stale population cannot be read as a live defect.
    ("session_signature",
     "MATCH (s:Session) WITH keys(s) AS k, count(*) AS n RETURN k, n ORDER BY n DESC"),
    ("session_turn_count_sum",
     "MATCH (s:Session) RETURN sum(coalesce(s.turn_count, 0)) AS n"),
    # #4211 review N1: the two facts the receipt's reader needs to decide whether the
    # embedded store is a LEGACY POPULATION or a live defect — the Object kind mix
    # (0 `pm:issue` ⇒ no GitHub indexer wrote them) and the `created_at` span. Both
    # stood in the PR body as an inference the artefact could not reproduce.
    ("object_kinds",
     "MATCH (o:Object) RETURN coalesce(o.objectKind, '<none>') AS objectKind, "
     "count(*) AS n ORDER BY n DESC"),
    ("session_created_at_range",
     "MATCH (s:Session) WHERE s.created_at IS NOT NULL "
     "RETURN count(s) AS n, min(s.created_at) AS min_created_at, "
     "max(s.created_at) AS max_created_at"),
    # ── what a turn -> Object -> turn walk could reach ──
    ("objects_total", "MATCH (o:Object) RETURN count(o) AS n"),
    # ⛔ A key NAMED `*turn*` applies the turn discriminator (see TURN), and the any-kind
    # reading is reported BESIDE it (`objects_with_gt1_point`) — the same discipline as
    # `sessions_with_gt1_contains_turn` / `sessions_with_gt1_contains_point`. This key
    # shipped UNFILTERED and counted any attached Point: on the committed receipt the
    # writer arm holds 9 Point->Object edges against 8 turns, the ninth hanging off an
    # extracted claim Point, so the reading was one attachment away from being wrong
    # (#13919 review round 7, item 3).
    ("objects_with_gt1_turn",
     f"MATCH (p:Point)-[:aboutObject]->(o:Object) WHERE {TURN} "
     "WITH o, count(DISTINCT p) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("objects_with_gt1_point",
     "MATCH (p:Point)-[:aboutObject]->(o:Object) "
     "WITH o, count(DISTINCT p) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("objects_reaching_gt1_session",
     "MATCH (s:Session)-[:aboutObject]->(o:Object) "
     "WITH o, count(DISTINCT s) AS c WHERE c > 1 RETURN count(*) AS n"),
    # Two SEPARATE questions — the earlier single key used ``OPTIONAL MATCH ... count(*)``,
    # which returns ``objects_total`` (count(*) counts the null row), so it could never
    # answer the question it was named for.
    ("objects_with_point_attachment",
     "MATCH (o:Object) WHERE (:Point)-[:aboutObject]->(o) RETURN count(o) AS n"),
    ("objects_with_any_attachment",
     "MATCH (o:Object) WHERE ()-[:aboutObject]->(o) RETURN count(o) AS n"),
    # ── the EVENT-level attachment: an :Event carries a ``session_id`` PROPERTY, so
    # Event->Object is a session->entity attachment with NO Session->Object edge.
    # Null-blindness is reported rather than assumed away.
    ("events_total", "MATCH (e:Event) RETURN count(e) AS n"),
    ("events_with_aboutObject",
     "MATCH (e:Event)-[:aboutObject]->(:Object) RETURN count(DISTINCT e) AS n"),
    ("event_session_ids_distinct",
     # ⛔ The NAME promises the whole Event population; the earlier Cypher filtered to
     # events that CARRY an `aboutObject` edge and published 1042 beside
     # `events_total: 10526` — a 10x-smaller, attachment-scoped answer under an
     # unscoped name, with the scoping visible only in a source comment (#13919 cycle
     # 6, the fourth of this class: a key readable two ways either carries the filter
     # in its name or publishes both readings, as the turn/any-kind pairs do).
     "MATCH (e:Event) WHERE e.session_id IS NOT NULL "
     "RETURN count(DISTINCT e.session_id) AS n"),
    ("event_attached_session_ids_distinct",
     "MATCH (e:Event)-[:aboutObject]->(:Object) "
     "RETURN count(DISTINCT e.session_id) AS n"),
    ("events_without_session_id",
     "MATCH (e:Event) WHERE e.session_id IS NULL RETURN count(e) AS n"),
    ("events_with_aboutObject_missing_session_id",
     "MATCH (e:Event)-[:aboutObject]->(:Object) WHERE e.session_id IS NULL "
     "RETURN count(e) AS n"),
    ("objects_reaching_gt1_event",
     "MATCH (:Event)-[:aboutObject]->(o:Object) "
     "WITH o, count(*) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("event_objects_total_distinct",
     "MATCH (:Event)-[:aboutObject]->(o:Object) RETURN count(DISTINCT o) AS n"),
]


def _clean(value):
    """JSON has no NaN/Infinity; a non-finite float becomes ``None`` (never a 0).

    ``toFloat(0)/0`` is NaN on an empty set, and a NaN in the receipt is a token
    ``json.loads(..., parse_constant=raise)``, Go and JS ``JSON.parse`` all reject —
    the artifact would fail to PARSE rather than report a wrong number.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _run_queries(g) -> dict:
    """Run every query; a failing query records an ERROR, never a 0."""
    out = {}
    for key, cypher in QUERIES:
        try:
            rows = g.query(cypher).result_set
        except Exception as exc:
            out[key] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if not rows:
            out[key] = None
        elif len(rows[0]) == 1:
            out[key] = _clean(rows[0][0])
        else:
            out[key] = _clean([list(r) for r in rows])
    return out


def _is_empty(counts: dict) -> bool | None:
    """A graph with no nodes is INDISTINGUISHABLE from a mistyped/absent graph name.

    ``select_graph()`` CREATES a graph on demand on both engines, so a typo yields
    exactly the zero shape a verdict of "there is nothing to walk" would report.
    The caller must name that state instead of publishing zeros as a measurement.

    ``None`` means UNKNOWN: ``nodes_by_label`` itself failed, so emptiness was never
    read. It is never folded into ``False`` ("not empty") — that would let a graph
    whose node read timed out publish its zeros as a measurement, the same fail-open
    class as an arm silently dropping a graph (`_note_unreadable_emptiness`).
    """
    nodes = counts.get("nodes_by_label")
    if isinstance(nodes, dict) and nodes.get("error"):
        return None
    return not nodes or nodes == []


def _failed_keys(counts: dict) -> list[str]:
    """Which queries the arm could not run — published so completeness is checkable."""
    return [k for k, v in counts.items() if isinstance(v, dict) and v.get("error")]


def _note_unreadable_emptiness(body: dict) -> dict:
    """An unreadable node count makes the arm UNMEASURABLE, not non-empty."""
    if body.get("empty_or_absent", False) is None:
        body["unmeasurable"] = (
            "nodes_by_label raised, so whether this graph is empty is UNKNOWN — the "
            "counts beside it cannot be read as a measurement of a graph that was read")
    return body


def _sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _file_identity(path: Path) -> dict:
    """sha256 + byte count from ONE pass over ONE file.

    #4211 review N3: ``source_sha256`` hashed the COPY (after the embedded engine had
    been pointed at it, so an RDB rewrite changed the value) while ``source_bytes``
    read the ORIGINAL — a pair a re-run cannot verify against, because its two halves
    described two files at two times. Both now come from the same read.
    """
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            total += len(chunk)
    return {"sha256": h.hexdigest(), "bytes": total}


def _module_digests() -> dict:
    """The digests of the product modules the measurement runs THROUGH (#4211 N7)."""
    return {rel: _sha256(_REPO_ROOT / rel) for rel in MEASURED_THROUGH}


def _head(rows: list[dict], limit: int) -> tuple[list[dict], int]:
    """(kept, omitted) — the omitted count is PUBLISHED, so the aggregate stays
    re-derivable from the artefact (#4211 N5: 25 of 60 rows were cut with no marker)."""
    return rows[:limit], max(0, len(rows) - limit)


def _every_graph_has_fewer_turns_than_contains(rows: list[dict]) -> bool | None:
    """``None`` over an EMPTY population.

    ``all([])`` is ``True``, so the old expression asserted "every graph satisfies X"
    with zero graphs measured — the same state this module refuses a few functions up
    (``_is_empty``) (#4211 review N4).
    """
    if not rows:
        return None
    return all(r["turns"] <= r["contains_edges"] for r in rows)


def _git_head() -> str | None:
    """The commit the run started from — a LABEL, never a claim that the tree was
    clean. ``instrument.sha256`` is what a re-run keys on, because this commit may
    predate the commit that carries the tool."""
    import subprocess
    try:
        return subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip() or None
    except Exception:
        return None


# ── ARM `store`: a read-only copy of a real embedded store ────────────────

def _open_embedded(path: Path):
    """The repo's OWN embedded choke-point, never a raw redislite construction.

    #4211 review N2: a direct ``redislite.falkordb_client`` FalkorDB import trips the
    repo's redis-guard (pattern 2) — CI is green over it because the workflow runs the
    guard with no argv, but pre-commit sees ``tools/`` — and it writes no owner record,
    so the reaper cannot confirm the server this arm starts. ``tortoise.FalkorDB`` is
    the guarded subclass: absolute-path only, idempotent ``close()`` and atexit
    registration (#1005), which is what makes the close in ``measure_store`` reachable.
    """
    from tortoise import FalkorDB
    from tortoise.projection import _embedded_retry, _socket_timeouts

    # ⛔ A read bound, written down rather than inherited (``falkordb`` defaults
    # ``socket_timeout=None`` and hands it to redis-py explicitly, so a bare
    # construction is UNBOUNDED: against a stalled engine this arm parks in
    # ``recv()`` forever — alive, 0% CPU, printing nothing — which for a tool whose
    # contract is "measure and change nothing" is indistinguishable from a slow
    # answer).
    #
    # ⛔ The EMBEDDED lane's policy, NOT ``_bounded_server_socket_kwargs()`` — that
    # dict's own docstring says it is "NOT for the embedded (unix-socket) lane", and
    # the reason is load-bearing here: redis-py DROPS ``socket_connect_timeout`` on
    # the unix-socket branch, so spreading the server pair at this call site would
    # wire a CONNECT bound that does not exist — a knob that reads as wired and does
    # nothing, which is the trap #3421 removed from the product's own embedded
    # branch. The embedded lane takes its read bound from ``_socket_timeouts()``
    # directly and its retry policy from ``_embedded_retry()`` — the same two helpers
    # ``projection``'s embedded branch resolves. One DELIBERATE divergence, stated so
    # it is not misread as parity (#13919 review F4): ``projection`` also calls
    # ``_apply_embedded_connect_timeout(self.db, connect_to)`` to deliver the CONNECT
    # bound to the POOL, and this site does not — a redislite handle spawns its own
    # local daemon, so there is no foreign host for that leg to bound. The READ bound,
    # which is what a stalled engine parks, is wired here.
    _connect_to, read_to = _socket_timeouts()
    return FalkorDB(str(path), socket_timeout=read_to, retry=_embedded_retry())


def measure_store(db_path: str, graph_name: str) -> dict:
    src = Path(os.path.expanduser(db_path))
    if not src.exists():
        return {"skipped": f"store not found at {src}"}
    # The identity is taken from the ORIGINAL, before the copy and before any engine
    # opens anything (N3) — ``source_bytes`` and ``source_sha256`` describe the same
    # file at the same moment.
    source = _file_identity(src)
    tmpdir = Path(tempfile.mkdtemp(prefix="m4211-store-"))
    dst = tmpdir / "store.db"
    t0 = time.time()
    shutil.copy2(src, dst)  # the ORIGINAL is never opened by the engine
    copy = _file_identity(dst)  # the bytes the engine is about to get, measured pre-open
    db = None
    try:
        db = _open_embedded(dst)
        names = [n for n in db.connection.execute_command("GRAPH.LIST")]
        if graph_name not in names:
            return {"skipped": f"graph {graph_name!r} is not among the {len(names)} "
                               "graphs in this file — refusing to select_graph() and "
                               "report the zeros it would invent",
                    "graphs_in_file": len(names)}
        g = db.select_graph(graph_name)
        counts = _run_queries(g)
        return _note_unreadable_emptiness({
            "source": str(src),
            "source_sha256": source["sha256"],
            "source_bytes": source["bytes"],
            "copy_matches_source": copy["sha256"] == source["sha256"],
            "read_only_copy": str(dst),
            "graph": graph_name,
            "graphs_in_file": len(names),
            "copy_s": round(time.time() - t0, 2),
            "counts": counts,
            "empty_or_absent": _is_empty(counts),
            "queries_failed": _failed_keys(counts),
            "queries": dict(QUERIES),
        })
    finally:
        # Close the embedded server BEFORE the directory it lives in is deleted —
        # ``rmtree`` under a still-running child is the shape that writes no owner
        # record for the reaper (#4211 review N2, second half).
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── ARM `live`: the graph the box is CONFIGURED to serve, read-only ───────

def _parse_uri(uri: str) -> dict:
    from urllib.parse import urlsplit
    parts = urlsplit(uri)
    if not parts.hostname:
        # ⛔ NEVER interpolate the input (#13919 review F2). `urlsplit` answers
        # `hostname=None, password=<secret>` for a host-less credentialed URI
        # (`docker://:hunter2@/tortoise`), so the refusal path is reachable with a
        # real password — and this message is serialised into the receipt (and its
        # traceback) by every arm that catches it. `_redact_uri` already answers
        # `<unparseable-uri>` for exactly this shape, so reuse it rather than
        # re-deriving a redaction that could drift from the one the cited arms use.
        raise ValueError(
            f"unparseable DB URI (userinfo redacted): {_redact_uri(uri)}")
    return {
        "host": parts.hostname,
        "port": parts.port or 6379,
        "password": parts.password or "",
        "graph": (parts.path or "/tortoise").lstrip("/") or "tortoise",
    }


def _open_live(uri: str):
    from falkordb import FalkorDB

    from tortoise.projection import _bounded_server_socket_kwargs

    spec = _parse_uri(uri)
    # Same bound, same reason as ``_open_embedded`` — and the same resolver, so an
    # operator's ``TORTOISE_DB_SOCKET_TIMEOUT`` reaches this arm instead of being
    # re-derived here (#3065's defect: every site spelling its own socket policy).
    return FalkorDB(host=spec["host"], port=spec["port"],
                    password=spec["password"] or None,
                    **_bounded_server_socket_kwargs()), spec


def _redact_uri(uri: str) -> str:
    """Re-emit a DB URI with its userinfo STRIPPED — never a credential in a receipt.

    #4211 review F2: ``measure_live`` and ``measure_instrument`` recorded the
    ``--live-uri`` STRING verbatim, and both are reachable with a credentialed URI (a
    re-run against the hosted surface the PR itself declares UNMEASURED). The receipt
    is committed under ``docs/scoping/receipts/``, so verbatim is a
    credential-in-git path. OWASP's logging guidance is to strip the URL userinfo;
    ``_writer_target`` already re-emits host/port/graph only, and this is the same
    mechanic for the two read arms that also serialise a URI.
    """
    from urllib.parse import urlsplit, urlunsplit
    try:
        parts = urlsplit(uri)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "<unparseable-uri>"
    if not host:
        return "<unparseable-uri>"
    return urlunsplit((parts.scheme, f"{host}:{port}" if port else host,
                       parts.path, "", ""))


#: argv flags whose VALUE is a connection string. Their recorded value is redacted: the
#: receipt (with its ``instrument.argv``) is committed under ``docs/scoping/receipts/``.
_URI_ARGV_FLAGS = ("--live-uri",)


def _redact_argv(argv: list[str]) -> list[str]:
    """The recorded command line with every connection-string VALUE redacted.

    #4211 review F2 named the three arms that serialised the raw URI and the fix redacted
    all of them. ``instrument.argv`` is a FOURTH site it did not reach, and live
    verification found it: every arm body can be redacted while
    ``--live-uri docker://:pw@host:6379/g`` still commits the password, because argv was
    recorded verbatim and the receipt is committed. MEASURED before this fix: a run with
    ``--live-uri docker://:hunter2@127.0.0.1:1/tortoise`` wrote ``hunter2`` at
    ``instrument.argv[3]``. The flag is kept — it is what makes a receipt reproducible —
    and only its value is re-emitted without userinfo.

    ⛔ And the exact-spelling match was DEFEATED by argparse ABBREVIATION (#13919 round
    8): ``allow_abbrev`` defaults to True, so ``--live-ur`` is accepted by the parser and
    then not recognised here, and the abbreviated flag's value went into the receipt
    verbatim. Fixed at both ends: the parser now sets ``allow_abbrev=False`` (so the
    recorded flag is canonical too), and ``redact_me`` carries the URI values this run
    actually resolved, so any token CONTAINING one is redacted whatever spelled it.
    """
    out: list[str] = []
    redact_next = False
    for a in argv:
        if redact_next:
            out.append(_redact_uri(a))
            redact_next = False
            continue
        matched = next((f for f in _URI_ARGV_FLAGS
                        if a == f or a.startswith(f + "=")), None)
        if matched is None:
            out.append(_redact_uri_in_token(a))
        elif a == matched:
            out.append(a)
            redact_next = True
        else:
            flag, _, value = a.partition("=")
            out.append(f"{flag}={_redact_uri(value)}")
    return out


def _redact_uri_in_token(token: str) -> str:
    """A token that is not an argv FLAG but may still BE (or contain) a URI value.

    #13919 round 8, defence in depth behind ``allow_abbrev=False``: the value of an
    abbreviated flag is a bare token with no flag to key on, so the only safe reading
    is "does this token carry a DB-URI-shaped value?". ``_redact_uri`` is applied when
    it does; anything else passes through untouched, so ordinary arguments are
    recorded verbatim and the receipt stays reproducible.
    """
    for scheme in ("docker://", "bolt://", "redis://", "rediss://"):
        idx = token.find(scheme)
        if idx < 0:
            continue
        head, rest = token[:idx], token[idx:]
        return head + _redact_uri(rest)
    return token


def measure_live(uri: str, uri_source: str | None = None) -> dict:
    db, spec = _open_live(uri)
    names = [n for n in db.connection.execute_command("GRAPH.LIST")]
    if spec["graph"] not in names:
        return {"skipped": f"graph {spec['graph']!r} not among the {len(names)} graphs "
                           "on this instance",
                "graphs_on_instance": len(names)}
    g = db.select_graph(spec["graph"])
    ro = getattr(g, "ro_query", None)
    counts = {}
    for key, cypher in QUERIES:
        try:
            rows = (ro(cypher) if ro else g.query(cypher)).result_set
        except Exception as exc:
            counts[key] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if not rows:
            counts[key] = None
        elif len(rows[0]) == 1:
            counts[key] = _clean(rows[0][0])
        else:
            counts[key] = _clean([list(r) for r in rows])
    return _note_unreadable_emptiness({
        # Redacted: host/port/graph survive, the userinfo does not (F2).
        "uri": _redact_uri(uri),
        "graph": spec["graph"],
        "read_only": f"ro_query={bool(ro)}",
        # What the artifact can actually prove about the role of this graph:
        "role_evidence": {
            # ⛔ MEASURED, not assumed: `~/.zshrc:48` was a string literal, so a re-run
            # with `--live-uri` published a receipt asserting a provenance nothing in
            # the run read (#4211 review F3). This says what actually supplied it.
            "uri_source": uri_source or "unspecified — measure_live() called directly, "
                                        "not through main()",
            "graphs_on_instance": len(names),
            "note": "uri_source says what SUPPLIED the URI; it does not prove where "
                    "that came from, and this tool never reads a shell profile. The "
                    "receipt records this graph's contents. It does NOT prove this is "
                    "the product's turn store — see the 'instrument' arm for the "
                    "sibling graphs on this instance that hold turns (that arm is the "
                    "one that COUNTS them; this block publishes only "
                    "`graphs_on_instance`, which is a different number and is "
                    "measured).",
        },
        "counts": counts,
        "empty_or_absent": _is_empty(counts),
        "queries_failed": _failed_keys(counts),
        "queries": dict(QUERIES),
    })


# ── ARM `instrument`: every graph on the instance that holds a turn store ──

def measure_instrument(uri: str) -> dict:
    """Aggregate every graph on the instance holding Sessions + episodic turns.

    This is the regime the W6B census measured (its "capture-shaped store" is
    ``org_default__default__<qid>``), reported at the scale the instance actually
    offers — so "0 aboutObject" is either reproduced across every such graph or
    contradicted, never inferred from one.
    """
    db, _spec = _open_live(uri)
    names = [n for n in db.connection.execute_command("GRAPH.LIST")]
    rows = []
    filtered: list[dict] = []
    errors: list[dict] = []
    inspected = 0
    for n in names:
        g = db.select_graph(n)
        try:
            sess = g.query("MATCH (s:Session) RETURN count(s)").result_set[0][0]
            if not sess:
                filtered.append({"graph": n, "reason": "no :Session"})
                continue
            turns = g.query(f"MATCH (p:Point) WHERE {TURN} RETURN count(p)"
                            ).result_set[0][0]
            if not turns:
                filtered.append({"graph": n, "reason": "no turn Point"})
                continue
            contains = g.query("MATCH (:Session)-[r:CONTAINS]->(:Point) "
                               "RETURN count(r)").result_set[0][0]
            # Multiplicity guard on the biggest number this arm publishes: a
            # double-wired CONTAINS (the #12244 write-bug class the gate cites) would
            # inflate a raw row count undetected. Pairs are measured, not inferred —
            # and measured by NODE IDENTITY, because the `s.id + '|' + p.id` spelling
            # is null-blind (a Point without an `id` makes the whole count 0).
            pairs = g.query("MATCH (s:Session)-[:CONTAINS]->(p:Point) "
                            "WITH DISTINCT s, p RETURN count(*)"
                            ).result_set[0][0]
            dupes = g.query("MATCH (s:Session)-[r:CONTAINS]->(p:Point) "
                            "WITH s, p, count(r) AS c WHERE c > 1 RETURN count(*)"
                            ).result_set[0][0]
            about = g.query("MATCH ()-[r:aboutObject]->() RETURN count(r)"
                            ).result_set[0][0]
            turn_about = g.query("MATCH (p:Point)-[:aboutObject]->(:Object) "
                                 f"WHERE {TURN} RETURN count(DISTINCT p)"
                                 ).result_set[0][0]
            kinds = g.query("MATCH (:Session)-[:CONTAINS]->(p:Point) "
                            "RETURN p.pointKind, count(*) ORDER BY count(*) DESC"
                            ).result_set
            inspected += 1
            rows.append({"graph": n, "sessions": sess, "turns": turns,
                         "contains_edges": contains,
                         "contains_distinct_pairs": pairs,
                         "contains_duplicate_pairs": dupes,
                         "aboutObject_edges": about,
                         "turns_with_aboutObject": turn_about,
                         "contains_targets_by_kind": [list(r) for r in kinds]})
        except Exception as exc:
            # ⛔ NEVER a silent drop: an exception on ANY of the eight queries removes
            # this graph from `rows`, hence from every total AND from the denominator
            # that makes "0 aboutObject in N of N" mean anything. The graph is named,
            # and a non-empty list makes the aggregate UNMEASURABLE rather than a
            # complete census with a shrunken denominator.
            errors.append({"graph": n, "error": f"{type(exc).__name__}: {exc}"})
            continue
    total = lambda k: sum(r[k] for r in rows)  # noqa: E731
    kinds: dict[str, int] = {}
    for r in rows:
        for kind, cnt in r["contains_targets_by_kind"]:
            kinds[str(kind)] = kinds.get(str(kind), 0) + cnt
    return _mark_shrunken_denominator({
        # Redacted: host/port/graph survive, the userinfo does not (F2).
        "uri": _redact_uri(uri),
        "graphs_on_instance": len(names),
        "graphs_with_turn_store": inspected,
        "graphs_without_turn_store": len(filtered),
        "graphs_skipped_on_error": len(errors),
        "skipped_without_turn_store": filtered[:25],
        "skipped_on_error": errors[:25],
        "totals": {
            "sessions": total("sessions"),
            "turns": total("turns"),
            "contains_edges": total("contains_edges"),
            "contains_distinct_pairs": total("contains_distinct_pairs"),
            "contains_duplicate_pairs": total("contains_duplicate_pairs"),
            "aboutObject_edges": total("aboutObject_edges"),
            "turns_with_aboutObject": total("turns_with_aboutObject"),
        },
        "contains_targets_by_kind": dict(sorted(kinds.items(),
                                                 key=lambda kv: -kv[1])),
        "graphs_with_any_aboutObject": sum(1 for r in rows if r["aboutObject_edges"]),
        "graphs_with_zero_aboutObject": sum(1 for r in rows if not r["aboutObject_edges"]),
        "every_graph_has_fewer_turns_than_contains":
            _every_graph_has_fewer_turns_than_contains(rows),
        "per_graph": _head(sorted(rows, key=lambda r: -(r["sessions"] + r["turns"])),
                           25)[0],
        "per_graph_omitted": _head(
            sorted(rows, key=lambda r: -(r["sessions"] + r["turns"])), 25)[1],
        "denominator_note": "graphs_on_instance = graphs_with_turn_store + "
                            "graphs_without_turn_store + graphs_skipped_on_error; a "
                            "non-zero error count makes every aggregate above a "
                            "SHRUNKEN denominator, never a complete census. "
                            "`per_graph_omitted` says how many rows the list does not "
                            "carry, so the aggregate stays re-derivable",
        # ⛔ The note is DERIVED from what this arm measured, not from a writer it
        # assumes (#13919 review round 7). The previous text named one writer
        # (``tools/ask_spotcheck.seed_capture_turn_store``) for the whole population,
        # while the arm's OWN ``contains_targets_by_kind`` shows a third of the CONTAINS
        # targets are ``session-transcript`` — a kind ``tortoise/retrieval.py`` records
        # as written ONLY by the eval ingest lane and explicitly NOT by that seeder
        # (which hardcodes ``"k": "event"``). Naming one writer for a mixed population
        # sent a reader looking for the wrong one, which is the asserted-vs-measured
        # class this PR already fixed for ``role_evidence.uri_source``.
        "note": _instrument_note(kinds),
    })


def _instrument_note(kinds: dict[str, int]) -> str:
    """The instrument arm's attribution, derived from the breakdown it MEASURED.

    #13919 review round 7: the note used to name one writer for the whole population,
    "the fixture seeder ... writes the Session->turn CONTAINS edges". The arm's own
    ``contains_targets_by_kind`` refutes that — on the censused population 12,606 of
    37,605 CONTAINS targets are ``session-transcript``, and ``tortoise/retrieval.py``
    records that kind as written ONLY by the eval ingest lane, "⚠️ NOR the ask-fixture
    seeder" (which hardcodes ``"k": "event"``). So the population is NOT one
    writer's output, and the honest note is the split this arm computed.

    What survives, and is what the note is FOR: the ask-fixture seeder and the **v1**
    ingest leg run no entity-link pass, so 0 ``aboutObject`` over that material describes
    THEM. The writer arm measures the product's own capture path separately, which is
    why the gate's answer does not depend on this note.

    ⛔ And the causal claim is NARROWER than it first read (#13919 review round 9).
    ``tortoise/retrieval.py``'s citation names the eval ingest lane as
    ``ingest.py`` / ``ingest_v2.py``, and the SUCCESSOR DOES wire the edge: ``#2165``
    (R8) — ``MERGE (e)-[:aboutObject]->(o)`` and the ``E7 (D7)``
    ``MERGE (p)-[:aboutObject]->(o)`` parity route, gated on ``about_entities``
    resolving to an existing ``:Object``. ``ingest.py`` carries zero ``aboutObject``
    writes. So this census's 0 also measures the ABSENCE OF v2 EXTRACTION OUTPUT in
    these graphs — a fact about the population, not a property of "the eval ingest
    lane". Saying otherwise would be the rounds-7/8 defect again, one layer down: the
    source check that stopped at ``ingest.py`` and missed its successor.
    """
    total = sum(kinds.values())
    if not total:
        return ("no CONTAINS targets were measured, so this run says nothing about "
                "which writer produced the population")
    parts = ", ".join(f"{kind}={cnt}" for kind, cnt in kinds.items())
    turn_n = kinds.get("event", 0)
    chunk_n = kinds.get("session-transcript", 0)
    return (
        f"CONTAINS targets by kind, MEASURED here: {parts} ({total} total; "
        f"{turn_n} event / {chunk_n} session-transcript). This population is not one "
        "writer's output — `tortoise/retrieval.py` records that "
        "`session-transcript` is written only by the eval ingest lane, and "
        "`tools/ask_spotcheck.py::seed_capture_turn_store` hardcodes `\"k\": \"event\"`, "
        "so it writes none of them. Neither of those — the seeder, nor the eval "
        "ingest V1 leg (`tools/longmem_eval/ingest.py`, zero `aboutObject` writes) — "
        "runs the entity-link pass, so 0 `aboutObject` here describes THEIR output. "
        "⛔ It does NOT describe the whole eval lane: its V2 leg "
        "(`tools/longmem_eval/ingest_v2.py`) DOES write the edge by its own parity "
        "route (#2165/E7-D7, gated on `about_entities`), so a census that reads 0 "
        "here also measures the ABSENCE OF v2 EXTRACTION OUTPUT in these graphs. "
        "The product's own capture path is what the `writer` arm measures, before "
        "and after, on a fresh graph.")


def _mark_shrunken_denominator(body: dict) -> dict:
    """A graph that RAISED — or a census that inspected NO graph — is not a measurement.

    ⛔ The COUNT comes from ``graphs_skipped_on_error`` — the authoritative total — not
    from ``skipped_on_error``, which the census truncates to 25. Reading the truncated
    list made the sentence say "25 of 173 graphs raised" beside a field a few lines
    above it that said 30, under-stating the incompleteness in the one sentence a
    reader acts on (#4211 review F4).

    ⛔ ZERO INSPECTED GRAPHS IS THE SAME CLASS OF NON-ANSWER, and it used to reach
    ``return 0`` (#13919 review round 7, item 1). An instance with no graphs has no
    error to count, so nothing was marked: the documented one-line re-run against a
    wrong/fresh container published ``graphs with a turn store: 0 of 0``, all-zero
    totals and ``every graph turns<=CONTAINS: None`` — and REPLACED the committed
    receipt, destroying the artifact of record while reporting success. ``_is_empty``
    states the principle for one graph (a zero shape must be NAMED, never published as
    a measurement); this is the same rule at the arm level.
    """
    errors = body.get("skipped_on_error") or []
    total = body.get("graphs_skipped_on_error")
    if total is None:  # a body that predates the counter still reports its list
        total = len(errors)
    if total:
        named = ", ".join(e["graph"] for e in errors[:5])
        more = "…" if total > len(errors[:5]) else ""
        body["unmeasurable"] = (
            f"{total} of {body.get('graphs_on_instance')} graphs raised during the "
            f"census ({named}{more}), so the aggregates are a SHRUNKEN denominator — "
            "'0 aboutObject in N of N' is NOT reproducible from this run")
    elif not body.get("graphs_with_turn_store"):
        # The denominator's OTHER zero: nothing raised, because nothing was READ. The
        # numbers below are a census of zero graphs — the shape a mistyped host, port or
        # password produces as readily as a real instance with no captures.
        body["unmeasurable"] = (
            "the census inspected NO graph: 0 of "
            f"{body.get('graphs_on_instance')} on the instance held a turn store "
            f"({body.get('graphs_without_turn_store')} filtered out, "
            f"{body.get('graphs_skipped_on_error')} raised), so every aggregate here "
            "is a census of ZERO graphs — a zero SHAPE, not a measurement of 'no "
            "aboutObject anywhere'. Check which instance this URI reached (host, "
            "port, password, graph) before reading the zeros as a finding")
    return body


# ── ARM `writer`: the REAL capture path ───────────────────────────────────

_ISSUE = "github.com/test/repo/issues/42"

CONV_REFERENCING = [
    {"role": "user", "content": "start on the retry logic for the ingest worker"},
    {"role": "assistant", "content": "reading the worker loop now"},
    {"role": "user",
     "content": f"the failure is tracked at {_ISSUE} — the backoff never resets"},
    {"role": "assistant", "content": "so the reset is missing after a success"},
    {"role": "user", "content": "right, and it compounds across restarts"},
    {"role": "assistant", "content": "I will add the reset on the success branch"},
    {"role": "user", "content": f"as discussed in {_ISSUE}, keep the guard"},
    {"role": "assistant", "content": "done — the guard is in place"},
]

CONV_NO_REFERENCE = [
    {"role": "user", "content": "why is the parse slow here"},
    {"role": "assistant", "content": "the tokenizer rebuilds its cache per call"},
    {"role": "user", "content": "can we hoist it out"},
    {"role": "assistant", "content": "yes, it is pure and safe to hoist"},
    {"role": "user", "content": "do it"},
    {"role": "assistant", "content": "hoisted, cached on the module"},
    {"role": "user", "content": "and the benchmark"},
    {"role": "assistant", "content": "4x faster on the sample"},
]

CONV_ALL_REFERENCING = [
    {"role": "user", "content": f"{_ISSUE} step 1: reproduce"},
    {"role": "assistant", "content": f"{_ISSUE} reproduced on the lane"},
    {"role": "user", "content": f"{_ISSUE} now bisect"},
    {"role": "assistant", "content": f"{_ISSUE} bisected to the write path"},
    {"role": "user", "content": f"{_ISSUE} fix it"},
    {"role": "assistant", "content": f"{_ISSUE} fixed, test added"},
]

#: per-session counts, each FILTERED to what it names: a shared extracted claim Point
#: (the offline extractor emits one canned claim for every session) must not be read
#: as "this session has an entity-linked turn".
_PER_SESSION = {
    "contains_turns":
        "MATCH (:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        f"WHERE {TURN} RETURN count(DISTINCT p)",
    "contains_points_all_kinds":
        "MATCH (:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "RETURN count(DISTINCT p)",
    "entity_linked_turns":
        "MATCH (:Session {id:$sid})-[:CONTAINS]->(p:Point)-[:aboutObject]->(:Object) "
        f"WHERE {TURN} RETURN count(DISTINCT p)",
    "session_aboutObject_edges":
        "MATCH (:Session {id:$sid})-[:aboutObject]->(:Object) RETURN count(*)",
    # a Point CONTAINS-wired by MORE THAN ONE session is shared structure, and must be
    # visible as such rather than silently inflating per-session counters.
    "shared_points":
        "MATCH (:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WITH p MATCH (s2:Session)-[:CONTAINS]->(p) "
        "WITH p, count(DISTINCT s2) AS c WHERE c > 1 RETURN count(DISTINCT p)",
}


def _delta(before: dict, after: dict) -> dict:
    out = {}
    for k in ("points_total", "turns_total", "contains_edges", "sessions_total",
              "aboutObject_by_endpoint", "turns_with_aboutObject",
              "sessions_with_aboutObject", "objects_total"):
        b, a = before.get(k), after.get(k)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            out[k] = a - b
    return out


def _looks_ephemeral(graph: str) -> bool:
    """Is this graph name a lane throwaway? SEGMENT-match, never substring.

    #13919 review F1: `any(marker in graph for marker in ...)` admitted
    "tortoise_latest", "prod_latest" and "contest_graph" — `test` is a substring of
    `latest` and of `contest` — so the N8 refusal failed open on exactly the
    production names it exists to protect. Splitting on the delimiters the fleet
    actually uses (`_`, `-`, `.`) keeps `m4211_hierarchy` / `ci_x` ephemeral and
    makes `latest` a segment of its own, not evidence of a test.
    """
    g = (graph or "").lower()
    segments = [s for s in re.split(r"[_\-.]", g) if s]
    return any(marker in segments for marker in EPHEMERAL_GRAPH_MARKERS)


def _writer_target(uri: str) -> tuple[dict | None, str | None]:
    """(target, refusal) — refuse rather than WRITE into a graph we cannot prove is a
    lane's throwaway (#4211 review N8).

    The READ arms already refuse when the target is not what the caller thinks it is
    (they will not ``select_graph()`` and publish the zeros it would invent). The arm
    that MUTATES had no equivalent check while being in the DEFAULT set:
    ``TORTOISE_DB_URI`` is the product's connection string, so a bare run annotated
    whatever graph it named with fixture sessions that every later census — including
    the one this tool exists to unlock — would count.
    """
    try:
        spec = _parse_uri(uri)
    except ValueError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    target = {"host": spec["host"], "port": spec["port"], "graph": spec["graph"]}
    if not _looks_ephemeral(spec["graph"]):
        return target, (
            f"refusing to WRITE into graph {spec['graph']!r} on {spec['host']}:{spec['port']}"
            " — the writer arm mints 3 fixture sessions ('m4211-*'), one shared claim "
            "Point and the Object 'test/repo#42' into whatever graph TORTOISE_DB_URI "
            "names, and every later census would then count them. A delimited SEGMENT "
            f"of the graph name must be one of {EPHEMERAL_GRAPH_MARKERS} (the name is "
            "split on '_', '-', '.'), e.g. 'm4211_hierarchy'; pass "
            "--allow-non-ephemeral-writes to write anyway (the receipt records the argv).")
    return target, None


def measure_writer(*, allow_non_ephemeral: bool = False) -> dict:
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        return {"skipped": "TORTOISE_DB_URI is not exported — run the lane seam first "
                           "(uri=\"$(uv run python tools/test_lane.py uri)\"; eval \"$uri\")"}
    target, refusal = _writer_target(uri)
    if refusal and (target is None or not allow_non_ephemeral):
        # `unmeasurable` as well as `skipped`: the reason has to be readable where a
        # reader looks for it (the top-level block, which `main` hoists), and a run
        # that REFUSED to write measured NOTHING — exiting 0 would let a
        # `--arms writer || exit 1` caller accept a refusal as a completed capture.
        return {"skipped": refusal, "unmeasurable": refusal, "target": target}
    # #4211: the offline extractor seam. It IS load-bearing for the extractor's own
    # Points (a canned claim per session) — so those are counted separately below and
    # never folded into the turn counts. The turn-level link pass runs on the STORED
    # TURN TEXTS (session_link.link_session_entities) and is mock-independent.
    os.environ.setdefault("TORTOISE_SESSION_LLM_MOCK", "1")
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    try:
        g = sdk._get_proj().g
        before = _run_queries(g)
        obj = sdk.create_object("test/repo#42", objectKind="pm:issue")
        captures, per_session = {}, {}
        for sid, conv in (
            ("m4211-ref-2of8", CONV_REFERENCING),
            ("m4211-noref", CONV_NO_REFERENCE),
            ("m4211-ref-6of6", CONV_ALL_REFERENCING),
        ):
            r = sdk._capture_session(conv, session_id=sid)
            captures[sid] = {
                "ok": r.get("ok"),
                "turns": r.get("turns"),
                "extraction_mode": r.get("extraction_mode"),
                "errors": r.get("errors"),
                "warnings": r.get("warnings"),
            }
        # ⛔ `per_session` is measured in a SECOND PASS, after every capture has run
        # (#13919 review round 9). Measuring it inside the loop made each row a
        # MID-RUN snapshot: the receipt's own numbers refuted it arithmetically —
        # `contains_points_all_kinds` 9/9/7 = 25 slots against `points_total` 23 (the
        # 3 `statement` slots are one claim Point wired into three sessions), so at
        # most one of those 2 shared slots can be session 1's, yet `shared_points`
        # read 0 for it because it was read before the later sessions existed. A row
        # that describes a moment the graph will never be in again is not a row about
        # the graph; an END-STATE reading is re-derivable against `counts`.
        per_session: dict[str, dict] = {}
        for sid in captures:
            per_session[sid] = {
                k: g.query(q, params={"sid": sid}).result_set[0][0]
                for k, q in _PER_SESSION.items()
            }
        after = _run_queries(g)
        replayed = [sid for sid, c in captures.items()
                    if c.get("extraction_mode") == "replayed"]
        delta = _delta(before, after)
        modes = {sid: c.get("extraction_mode") for sid, c in captures.items()}
        body = {
            # WHERE it wrote, not only which graph NAME: the receipt used to carry
            # `uri_graph` alone, so a lane container and a served store were
            # indistinguishable in the artefact. Never the password.
            "target": target,
            "non_ephemeral_write_override": bool(refusal and allow_non_ephemeral),
            # ⛔ The PARSED graph segment, never `uri.rsplit("/", 1)[-1]`: on a
            # path-less URI (`docker://:pw@db:6379`, a shape `_parse_uri` supports) the
            # slice answers the whole AUTHORITY, so the password reached the receipt
            # from the one arm whose own `target` block omits it (#4211 review F2).
            "uri_graph": target["graph"],
            "sdk": "TortoiseSDK() against TORTOISE_DB_URI",
            "object_mint": {"name": "test/repo#42", "kind": "pm:issue",
                            "advisory": obj.get("kind_layer_routed")
                            or obj.get("kind_redirect")},
            "captures": captures,
            "per_session": per_session,
            "counts": after,
            "counts_before_any_write": before,
            "delta_this_run": delta,
            # Which queries the arm could not run — the writer was the one arm with no
            # such accounting, so an unreadable population was invisible here while it
            # was published by `measure_live` and `measure_store` (#13919 round 6).
            # ⛔ BOTH sides, tagged (#13919 round 7): the verdict below reads
            # `_failed_keys(after) or _failed_keys(before)`, so reporting only `after`
            # let one artefact say `unmeasurable: "...not readable (before-side keys)"`
            # AND `queries_failed: []` — a consumer reading the empty list reads "all "
            # queries ran".
            "queries_failed": {"before": _failed_keys(before),
                               "after": _failed_keys(after)},
            # The extraction MODE per session, recorded so a reader can tell a mocked
            # extraction from a keyless one without re-running (#13919 review F1).
            "extraction_modes": modes,
            "queries": dict(QUERIES),
            "per_session_queries": _PER_SESSION,
        }
        # ── did this run WRITE anything? Ask the INVARIANT, not a label ──────
        # #13919 review F1: the previous test was `extraction_mode == "replayed"`,
        # and that token is not the invariant. `TortoiseSDK._capture_session` puts
        # its KEYLESS branch BEFORE the #1727 replay branch on purpose
        # (`tortoise/sdk.py`, "a keyless call must ALWAYS be reported as keyless,
        # never as a silent 'replayed'"), so a replay performed under a keyless
        # extraction reports `no-provider` — `replayed` stayed empty, no
        # `unmeasurable` was set, and `--out <receipt> || exit 1` returned 0 and
        # OVERWROTE the committed receipt with pre-existing counts. Reachable
        # because the mock is armed with `os.environ.setdefault`, which does not
        # override an ambient `TORTOISE_SESSION_LLM_MOCK` that is present but not
        # "1".
        #
        # The fixture writes three known sessions, so "this run wrote nothing" is
        # decidable from the delta alone — the numbers this arm ALREADY measured
        # and then ignored. A first run under the same keyless env still writes its
        # turns (the mechanical turn loop and link pass are mock-independent), so
        # this cannot mask a real capture.
        # ⛔ And an UNREADABLE delta is not a delta of zero (#13919 review round 6).
        # `_delta` emits a key only when BOTH sides are numbers, and `_run_queries`
        # turns a raising query into `{"error": …}` — so a count that timed out
        # leaves the key ABSENT, `delta.get(k)` is `None`, and `None == 0` is False:
        # the guard failed open again, by exactly the failure mode this PR's own read
        # bound produces on a 25k-turn store. Unreadable must be a verdict, not a
        # skipped branch — the writer arm is the only one with no `queries_failed`
        # accounting, and `measure_live`/`measure_store` both publish it.
        # ⛔ And a PARTIAL read is not a measurement here EITHER (#13919 cycle 6).
        # The gate above reads `turns_total` and `points_total` out of 39 queries, so a
        # run where those two cheap counts answered and `aboutObject_by_endpoint`'s
        # UNWIND or `contains_targets_by_kind` timed out on a 25k-turn store reached a
        # verdict on 2 of 39 keys — exit 0, no `.unmeasurable.json` sibling, and
        # `--out <receipt> || exit 1` replaced the artifact of record with an object
        # whose own counts are `{"error": …}` dicts. That is verbatim the round-9 harm,
        # closed for `live`/`store` with zero tolerance and left open on this arm — and
        # `_could_not_measure` skips the arm because this field is a MAPPING, not a
        # list, so nothing downstream caught it either. The rule is the other two
        # arms' rule: ONE failed key makes the run unmeasurable.
        failed_any = _failed_keys(before) or _failed_keys(after)
        turns_delta = delta.get("turns_total")
        points_delta = delta.get("points_total")
        if failed_any:
            body["unmeasurable"] = (
                f"{len(failed_any)} of {len(QUERIES)} queries raised before/after the "
                f"capture ({', '.join(failed_any[:5])}"
                f"{', …' if len(failed_any) > 5 else ''}) — a partial read is not a "
                "measurement")
        elif turns_delta is None or points_delta is None:
            body["unmeasurable"] = (
                "the before/after counts are not readable (turns_total/points_total "
                "absent from a three-session fixture run) — a delta that could not be "
                "read is not a delta of zero, so this run cannot be reported as a "
                "measurement")
        elif turns_delta == 0 and points_delta == 0:
            # A replay proves nothing about a writer: nothing was written. The numbers
            # that follow are the PRE-EXISTING content of this graph.
            why = ("sessions replayed, not captured: " + ", ".join(replayed)
                   if replayed else
                   "this run wrote NOTHING (no new turns and no new points) — the "
                   "three fixture session ids were already in this graph, so the "
                   "counts describe an earlier run")
            body["unmeasurable"] = (
                why + ". Extraction modes observed: " +
                ", ".join(f"{sid}={mode}" for sid, mode in sorted(modes.items())) +
                ". Re-run on a FRESH graph name.")
        return body
    finally:
        sdk.close()


# ── ARM `corpus`: the capture journal the turn layer WOULD be projected from ──

def measure_corpus(sessions_dir: str) -> dict:
    """Count the hierarchy in the SOURCE OF TRUTH (~/.tortoise/session-events).

    Turns are counted per-RECORD occurrences AND as DISTINCT per session: one session
    writes one record per end-of-session capture, each carrying the whole transcript,
    so the occurrence count over-counts (measured: 1.67 records per session).
    Ref density is measured on DISTINCT turns, with the PRODUCTION extractor
    (``session_link.extract_refs``), never a reimplementation.
    """
    import ast

    from tortoise.session_link import extract_refs

    d = Path(os.path.expanduser(sessions_dir))
    files = sorted(d.glob("*.jsonl"))
    if not files:
        return {"skipped": f"no session events under {d}"}
    stats = {"files": len(files), "records": 0, "parse_errors": 0,
             "records_without_text": 0, "turn_occurrences": 0,
             "turn_occurrences_with_ref": 0,
             # ⛔ A record with no session id was silently given a SYNTHETIC one
             # (`<file>:<n>`), i.e. it became its own session bucket — and nothing
             # counted that (#13919 review round 9). It is a real record class: the
             # drain names `no_session_id` / `no_conversation` / `unparseable_record`
             # as the shapes it must classify (`tortoise/session_events_drain.py`).
             # Uncounted, the artifact cannot say whether `sessions` and
             # `records_per_session` were computed over real ids or invented ones, and
             # `sessions_with_gt1_turn` is inflated by every such split, because one
             # session's transcript arriving as N records becomes N "sessions" each
             # holding the whole transcript. The two NEIGHBOURING degradations are
             # already counted; this one now is too.
             "records_without_session_id": 0}
    per_session: dict[str, dict] = {}
    for f in files:
        with f.open(errors="replace") as fh:
            for line in fh:
                stats["records"] += 1
                try:
                    rec = json.loads(line)
                except Exception:
                    stats["parse_errors"] += 1
                    continue
                conv = rec.get("conversation") or []
                if isinstance(conv, str):
                    # Recorded separately: if the journal format ever ships a string,
                    # this must be visible, not silently counted as "no text".
                    try:
                        conv = ast.literal_eval(conv)
                        stats.setdefault("conversation_as_string", 0)
                        stats["conversation_as_string"] += 1
                    except Exception:
                        conv = []
                turns = [t for t in conv
                         if isinstance(t, dict) and isinstance(t.get("content"), str)]
                if not turns:
                    stats["records_without_text"] += 1
                    continue
                sid = rec.get("session_id") or rec.get("sessionId")
                if not sid:
                    # Counted before the fallback makes it invisible: the synthetic id
                    # keeps the record in the tally (dropping it would understate the
                    # corpus) but the ARTIFACT now says how many were invented.
                    stats["records_without_session_id"] += 1
                    sid = f"{f.name}:{stats['records']}"
                row = per_session.setdefault(sid, {"turns": set(), "ref_turns": set()})
                for t in turns:
                    key = (t.get("role"), t["content"])
                    stats["turn_occurrences"] += 1
                    row["turns"].add(key)
                    if extract_refs(t["content"]):
                        stats["turn_occurrences_with_ref"] += 1
                        row["ref_turns"].add(key)
    s = list(per_session.values())
    turns_distinct = sum(len(r["turns"]) for r in s)
    turns_ref_distinct = sum(len(r["ref_turns"]) for r in s)
    return {
        "source": str(d),
        "stats": stats,
        "sessions": len(s),
        "turns_distinct": turns_distinct,
        "turns_naming_a_ref_distinct": turns_ref_distinct,
        "sessions_with_gt1_turn": sum(1 for r in s if len(r["turns"]) > 1),
        "sessions_with_gt1_ref_turn": sum(1 for r in s if len(r["ref_turns"]) > 1),
        "sessions_with_any_ref_turn": sum(1 for r in s if r["ref_turns"]),
        "max_turns_per_session": max((len(r["turns"]) for r in s), default=0),
        "records_per_session": round(stats["records"] / len(s), 3) if s else None,
        "ref_density_note": "a turn 'names a ref' when the PRODUCTION resolver "
                            "session_link.extract_refs matches; it can still resolve to "
                            "nothing if no matching Object exists in the graph",
    }


def _collect_warnings(receipt: dict) -> list[str]:
    """Populate ``warnings`` — it was initialised and never written (#4211 R2/N5).

    A skip recorded only inside an arm body is invisible to a reader who looks at the
    receipt's summary fields.
    """
    out = []
    for arm, body in (receipt.get("arms") or {}).items():
        if isinstance(body, dict) and body.get("skipped"):
            out.append(f"{arm}: skipped — {body['skipped']}")
    inst = (receipt.get("arms") or {}).get("instrument")
    if isinstance(inst, dict) and inst.get("per_graph_omitted"):
        out.append(f"instrument: per_graph carries {len(inst['per_graph'])} of "
                   f"{inst['graphs_with_turn_store']} rows; "
                   f"{inst['per_graph_omitted']} omitted (totals still cover all of them)")
    return out


def _publish(receipt: dict, out: str | None) -> Path | None:
    """Write the receipt — but an UNMEASURABLE run never replaces a good one.

    A re-run is commonly scripted ``… --out <receipt> || exit 1``. If the re-run cannot
    measure (a replay, a raising graph) it must not overwrite the receipt it failed to
    reproduce: the unmeasurable artifact goes to ``<name>.unmeasurable.json`` beside it,
    and the path actually written is printed.
    """
    if not out:
        return None
    target = Path(out)
    if receipt.get("unmeasurable") and target.exists():
        try:
            prior = json.loads(target.read_text())
        except Exception:
            prior = None
        if isinstance(prior, dict) and not prior.get("unmeasurable"):
            target = target.with_suffix(".unmeasurable" + target.suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, indent=1, allow_nan=False) + "\n")
    print(f"receipt: {target}")
    return target


def _could_not_measure(body) -> str | None:
    """The reason this arm measured NOTHING, or ``None`` if it measured something.

    ⛔ A PARTIAL read is not a measurement (#13919 review round 9).
    ``measure_live`` and ``measure_store`` publish ``queries_failed`` and nothing
    consulted it: ``_note_unreadable_emptiness`` set ``unmeasurable`` only when
    ``nodes_by_label`` ITSELF raised, so a read where that one query answered and all
    the others timed out published as MEASURED — exit 0, no ``.unmeasurable.json``
    sibling, and ``--out <receipt> || exit 1`` then destroyed the artifact of record.
    That is the same harm this file already closes for a SKIPPED arm and for a
    zero-graph census; the two read arms were the gap between them.

    The ``writer`` arm publishes a ``{before, after}`` mapping and reaches its own
    verdict, so only the LIST shape is handled here. Module-level rather than nested in
    ``main`` so the arms' verdict is directly pinnable.
    """
    if not isinstance(body, dict):
        return None
    if body.get("unmeasurable"):
        return body["unmeasurable"]
    if body.get("skipped") and "counts" not in body:
        return body["skipped"]
    failed = body.get("queries_failed")
    if isinstance(failed, list) and failed:
        return (f"{len(failed)} of {len(QUERIES)} queries raised "
                f"({', '.join(failed[:5])}"
                f"{', …' if len(failed) > 5 else ''}) — a partial read is not a "
                "measurement")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                allow_abbrev=False)
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--store-graph", default=DEFAULT_STORE_GRAPH)
    # None (not the constant) so the receipt can say what SUPPLIED the URI (#4211 F3).
    ap.add_argument("--live-uri", default=None,
                    help=f"the read-only instance to measure (default: the built-in "
                         f"{DEFAULT_LIVE_URI!r})")
    ap.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR)
    ap.add_argument("--arms", default=DEFAULT_ARMS,
                    help="comma-separated subset of store,writer,live,instrument,corpus "
                         f"(default: {DEFAULT_ARMS} — the READ-ONLY set; 'writer' "
                         "MUTATES the graph TORTOISE_DB_URI names and is opt-in)")
    ap.add_argument("--allow-non-ephemeral-writes", action="store_true",
                    help="let the writer arm write into a graph whose name does not "
                         "look ephemeral (default: refuse)")
    ap.add_argument("--out", default=None, help="receipt JSON path")
    args = ap.parse_args(argv)
    # The argv this run actually saw: `sys.argv[1:]` is the right default, but a
    # programmatic `main([...])` call (every test) must record ITS argv, not the
    # interpreter's — and the value is redacted before it reaches the receipt (F2).
    parsed_argv = list(argv) if argv is not None else sys.argv[1:]

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in KNOWN_ARMS]
    if unknown or not arms:
        # A typo used to fall through every `if "<arm>" in arms` and produce an EMPTY
        # receipt reported as a MEASUREMENT (exit 0) — the same empty-population
        # fail-open `_is_empty` and `every_graph_has_fewer_turns_than_contains` refuse.
        ap.error(f"no measurable arm: {args.arms!r} — "
                 f"unknown {unknown}; choose from {','.join(KNOWN_ARMS)}")
    not_requested = [a for a in KNOWN_ARMS if a not in arms]
    receipt: dict = {
        "issue": 4211,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code_head": _git_head(),
        "instrument": {
            "path": "tools/measure_4211_session_hierarchy.py",
            "sha256": _sha256(_TOOL_PATH),
            "measured_through": _module_digests(),
            "argv": _redact_argv(parsed_argv),
            # ⛔ What RAN versus what was never ASKED FOR. The default set is read-only
            # (`writer` is opt-in), so the documented one-line re-run used to produce a
            # receipt with no writer arm and nothing in the artifact saying why — an
            # absent arm is indistinguishable from a skipped one without this.
            "arms_requested": arms,
            "arms_not_requested": not_requested,
            "note": "code_head is the tree this run STARTED from; it may predate the "
                    "commit that carries this file, so the sha256 is what a re-run keys "
                    "on. measured_through pins the product modules the numbers come "
                    "THROUGH (the writer/corpus arms call into them), so an SDK move "
                    "is distinguishable from a graph move. arms_not_requested is NOT a "
                    "missing measurement: reproducing the committed receipt requires "
                    "--arms store,writer,live,instrument,corpus",
        },
        "purpose": "does a session-hierarchy structure exist for the #4211 "
                   "candidate-expansion arm to walk?",
        "definitions": {
            "turn": TURN,
            "hierarchy_linked_turn": "a turn Point reachable from its Session via "
                                     "(:Session)-[:CONTAINS]->(:Point) — the edge the "
                                     "scoping doc's arm walks",
            "entity_linked_turn": "a turn Point carrying "
                                  "(:Point)-[:aboutObject]->(:Object) — entity "
                                  "attachment, NOT the hierarchy",
            "event_level_attachment":
                "an :Event carries a session_id PROPERTY, so (:Event)-[:aboutObject]->"
                "(:Object) is a session->entity attachment with NO Session->Object edge; "
                "counted separately, never folded into the Session counts",
        },
        "edges_the_arm_could_walk": {
            "contains": "the hierarchy (Session -> turns) the scoping doc's arm uses",
            "aboutObject": "entity attachment; the edge the #4211 gate names. NOT the "
                           "hierarchy — a graph can have one and not the other",
        },
        "arms": {},
        "warnings": [],
    }
    live_uri = args.live_uri or DEFAULT_LIVE_URI
    # ⛔ What actually supplied it — a measured fact, not the constant's comment.
    live_uri_source = (
        "--live-uri (the caller passed this URI; nothing in this run proves where it "
        "came from)" if args.live_uri else
        f"built-in DEFAULT_LIVE_URI {DEFAULT_LIVE_URI!r} (documented at ~/.zshrc:48, "
        "but this tool reads no shell profile, so that provenance is asserted by the "
        "constant and NOT measured)")
    try:
        if "store" in arms:
            receipt["arms"]["store"] = measure_store(args.store, args.store_graph)
        if "writer" in arms:
            receipt["arms"]["writer"] = measure_writer(
                allow_non_ephemeral=args.allow_non_ephemeral_writes)
        if "live" in arms:
            receipt["arms"]["live"] = measure_live(live_uri, uri_source=live_uri_source)
        if "instrument" in arms:
            receipt["arms"]["instrument"] = measure_instrument(live_uri)
        if "corpus" in arms:
            receipt["arms"]["corpus"] = measure_corpus(args.sessions_dir)
    except Exception as exc:
        import traceback
        receipt["unmeasurable"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-3000:],
        }
        _publish(receipt, args.out)
        print(f"UNMEASURABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # An arm that could not measure is a TOP-LEVEL verdict, not a nested note. The
    # documented contract (0 = measured) reads the top-level block, and a replay that
    # exits 0 is accepted as a SUCCESSFUL measurement by any `|| exit 1` caller — the
    # flag being nested is what let a replayed run publish an earlier run's numbers.
    # ⛔ A `skipped` arm measured NOTHING, so it belongs in the same verdict: five
    # refusal paths (store absent, graph not in the file, no TORTOISE_DB_URI, no capture
    # journal, graph not on the instance) returned a bare `{"skipped": …}`, collected
    # nothing here, and reached `return 0` — i.e. a run that performed NO measurement
    # reported itself as a completed capture to a `--out <receipt> || exit 1` caller, and
    # (having no `unmeasurable`) was allowed to REPLACE the committed receipt with the
    # empty artefact (#4211 review F1).
    unmeasurable_arms = {
        arm: reason
        for arm, body in receipt["arms"].items()
        if (reason := _could_not_measure(body))
    }
    if unmeasurable_arms:
        receipt["unmeasurable"] = {
            "arms": unmeasurable_arms,
            "note": "at least one arm could not measure; a 'skipped' arm measured "
                    "nothing at all, and where an arm still reports numbers they "
                    "describe PRE-EXISTING graph content, never this run's capture",
        }

    receipt["warnings"] = _collect_warnings(receipt)
    _publish(receipt, args.out)

    for arm in ("store", "writer", "live"):
        body = receipt["arms"].get(arm)
        if not isinstance(body, dict):
            continue
        if "counts" not in body:
            print(f"{arm}: {str(body)[:400]}")
            continue
        c = body["counts"]
        print(f"\n== arm {arm} ({body.get('graph') or body.get('uri_graph')}) ==")
        print("  sessions / turns        :", c.get("sessions_total"), "/",
              c.get("turns_total"))
        print("  CONTAINS edges / pairs  :", c.get("contains_edges"), "/",
              c.get("contains_distinct_pairs"), "dupes:",
              c.get("contains_duplicate_pairs"))
        print("  sessions w/ >1 turn     :", c.get("sessions_with_gt1_contains_turn"))
        print("  sessions w/ >1 CONTAINS pt:", c.get("sessions_with_gt1_contains_point"),
              "(any point kind — the difference above is NOT a turn count)")
        print("  aboutObject by endpoint :", c.get("aboutObject_by_endpoint"))
        print("  turns w/ aboutObject    :", c.get("turns_with_aboutObject"))
        print("  objects by kind         :", c.get("object_kinds"))
        print("  session created_at span :", c.get("session_created_at_range"))
        print("  CONTAINS targets by kind:", c.get("contains_targets_by_kind"))
        if c.get("queries_failed"):
            print("  ⛔ queries that RAISED   :", c.get("queries_failed"))
        print("  events/w-aboutObject    :", c.get("events_total"), "/",
              c.get("events_with_aboutObject"))
        if body.get("delta_this_run"):
            print("  DELTA this run          :", body["delta_this_run"])
        if body.get("unmeasurable"):
            print("  ⛔ UNMEASURABLE          :", body["unmeasurable"])
    inst = receipt["arms"].get("instrument")
    if isinstance(inst, dict) and "totals" in inst:
        print("\n== arm instrument (all turn-store graphs on the instance) ==")
        print("  graphs with a turn store:", inst["graphs_with_turn_store"], "of",
              inst["graphs_on_instance"], "(filtered:",
              inst.get("graphs_without_turn_store"), "raised:",
              inst.get("graphs_skipped_on_error"), ")")
        print("  totals                  :", inst["totals"])
        print("  graphs with 0 aboutObject:", inst["graphs_with_zero_aboutObject"])
        print("  CONTAINS targets by kind:", inst["contains_targets_by_kind"])
        print("  per_graph rows / omitted:", len(inst.get("per_graph") or []), "/",
              inst.get("per_graph_omitted"))
        print("  every graph turns<=CONTAINS:",
              inst.get("every_graph_has_fewer_turns_than_contains"))
        if inst.get("skipped_on_error"):
            print("  ⛔ graphs that RAISED    :", inst["skipped_on_error"])
        if inst.get("unmeasurable"):
            print("  ⛔ UNMEASURABLE          :", inst["unmeasurable"])
    corp = receipt["arms"].get("corpus")
    if isinstance(corp, dict) and "stats" in corp:
        print("\n== arm corpus (~/.tortoise/session-events) ==")
        print("  files / records         :", corp["stats"]["files"], "/",
              corp["stats"]["records"])
        print("  sessions                :", corp["sessions"])
        print("  turns distinct          :", corp["turns_distinct"])
        print("  turns naming a ref      :", corp["turns_naming_a_ref_distinct"])
        print("  sessions w/ >1 turn     :", corp["sessions_with_gt1_turn"])
    if receipt.get("unmeasurable"):
        print("\nUNMEASURABLE — at least one arm could not measure:", file=sys.stderr)
        for arm, reason in (receipt["unmeasurable"].get("arms") or {}).items():
            print(f"  {arm}: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
