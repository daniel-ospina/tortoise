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
``live``        the graph ``~/.zshrc:48`` configures (``docker://:@localhost:16379/tortoise``)
                — labelled as exactly that, because the receipt can prove the URI string
                lives in a shell profile and nothing more. Read-only (``ro_query``).
``instrument``  every graph in that container holding Sessions + episodic turns, aggregated.
                This is the REGIME THE CENSUS MEASURED, at scale — those stores are written
                by the fixture seeder (``tools/ask_spotcheck.seed_capture_turn_store``), which
                does not run the entity-link pass, so the arm is what tells them apart from
                a graph the real writer produced.
``corpus``      the capture journal ``~/.tortoise/session-events`` (no graph). Turns are
                counted BOTH ways — per-record occurrences AND distinct per session —
                because one session writes one record per end-of-session capture, each
                carrying the whole transcript, so occurrences over-count.

Usage
-----
    uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"     # never a bare pytest
    TORTOISE_DB_URI="docker://:@127.0.0.1:${TORTOISE_TEST_DOCKER_PORT}/m4211_hierarchy" \\
      uv run python tools/measure_4211_session_hierarchy.py \\
        --out docs/scoping/receipts/2026-11-19-4211-hierarchy-remeasure.json

Exit codes: 0 = MEASURED (whatever the numbers are), 2 = could not measure — the reason is
in the receipt's TOP-LEVEL ``unmeasurable`` block, which is written whenever ANY arm could
not measure: a replayed writer, a graph that raised during the census, an unreadable node
count. A ``--out`` receipt is never replaced by an unmeasurable run (it writes
``<name>.unmeasurable.json`` beside it), so a re-run scripted as ``--out <receipt> ||
exit 1`` cannot overwrite the receipt it failed to reproduce.

Definition discipline (#4211 review round 1): a *turn* is
``coalesce(p.is_episodic,false)=true AND p.pointKind='event'`` — the product's own
discriminator (``tortoise/sdk.py``). A bare ``is_episodic`` also matches extracted
episodic MEMORY points, which are not turns; both counts are reported so the difference
is visible instead of silent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
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
     "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
     "RETURN count(DISTINCT s.id + '|' + p.id) AS n"),
    ("contains_duplicate_pairs",
     "MATCH (s:Session)-[r:CONTAINS]->(p:Point) "
     "WITH s, p, count(r) AS c WHERE c > 1 RETURN count(*) AS n"),
    ("sessions_with_contains",
     "MATCH (s:Session)-[:CONTAINS]->(:Point) "
     "RETURN count(DISTINCT s) AS n"),
    # A session CONTAINS-wires MORE than its turns: the extraction lane wires claim
    # Points into the same session, and the fixture seeder writes a
    # 'session-transcript' Point per session (both ``is_episodic``). Without this
    # breakdown a turn count and a CONTAINS count look like they disagree.
    ("contains_targets_by_kind",
     "MATCH (:Session)-[:CONTAINS]->(p:Point) "
     "RETURN p.pointKind AS pointKind, coalesce(p.is_episodic, false) AS episodic, "
     "count(*) AS n ORDER BY n DESC"),
    # ⛔ A key NAMED `*turn*` applies the turn discriminator, and the any-kind reading is
    # reported BESIDE it (``sessions_with_gt1_contains_point``) — the same discipline as
    # ``turns_total`` / ``episodic_points_any_kind`` above. Without the filter these keys
    # count a session's claim Points and its 'session-transcript' Point as turns, so on a
    # graph of one turn + one claim Point per session the gate's own falsifier
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
     "MATCH (s:Session)-[:aboutObject]->(o:Object) "
     "WITH s, count(DISTINCT o) AS c WHERE c > 1 RETURN count(*) AS n"),
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
    # ── what a turn -> Object -> turn walk could reach ──
    ("objects_total", "MATCH (o:Object) RETURN count(o) AS n"),
    ("objects_with_gt1_turn",
     "MATCH (t:Point)-[:aboutObject]->(o:Object) "
     "WITH o, count(DISTINCT t) AS c WHERE c > 1 RETURN count(*) AS n"),
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

def measure_store(db_path: str, graph_name: str) -> dict:
    src = Path(os.path.expanduser(db_path))
    if not src.exists():
        return {"skipped": f"store not found at {src}"}
    tmpdir = Path(tempfile.mkdtemp(prefix="m4211-store-"))
    dst = tmpdir / "store.db"
    t0 = time.time()
    shutil.copy2(src, dst)  # the ORIGINAL is never opened by the engine
    try:
        from redislite.falkordb_client import FalkorDB
        db = FalkorDB(str(dst))
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
            "source_sha256": _sha256(dst),
            "source_bytes": src.stat().st_size,
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
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── ARM `live`: the graph the box is CONFIGURED to serve, read-only ───────

def _parse_uri(uri: str) -> dict:
    from urllib.parse import urlsplit
    parts = urlsplit(uri)
    if not parts.hostname:
        raise ValueError(f"unparseable DB URI: {uri!r}")
    return {
        "host": parts.hostname,
        "port": parts.port or 6379,
        "password": parts.password or "",
        "graph": (parts.path or "/tortoise").lstrip("/") or "tortoise",
    }


def _open_live(uri: str):
    from falkordb import FalkorDB
    spec = _parse_uri(uri)
    return FalkorDB(host=spec["host"], port=spec["port"],
                    password=spec["password"] or None), spec


def measure_live(uri: str) -> dict:
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
        "uri": uri,
        "graph": spec["graph"],
        "read_only": f"ro_query={bool(ro)}",
        # What the artifact can actually prove about the role of this graph:
        "role_evidence": {
            "uri_source": "~/.zshrc:48",
            "graphs_on_instance": len(names),
            "note": "this receipt proves the URI STRING lives in a shell profile and "
                    "records this graph's contents. It does NOT prove this is the "
                    "product's turn store — see 'instrument' for the 60 sibling graphs "
                    "on the same instance that hold turns.",
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
            # inflate a raw row count undetected. Pairs are measured, not inferred.
            pairs = g.query("MATCH (s:Session)-[:CONTAINS]->(p:Point) "
                            "RETURN count(DISTINCT s.id + '|' + p.id)"
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
        "uri": uri,
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
        "every_graph_has_fewer_turns_than_contains": all(
            r["turns"] <= r["contains_edges"] for r in rows),
        "per_graph": sorted(rows, key=lambda r: -(r["sessions"] + r["turns"]))[:25],
        "denominator_note": "graphs_on_instance = graphs_with_turn_store + "
                            "graphs_without_turn_store + graphs_skipped_on_error; a "
                            "non-zero error count makes every aggregate above a "
                            "SHRUNKEN denominator, never a complete census",
        "note": "these stores are written by the fixture seeder "
                "(tools/ask_spotcheck.seed_capture_turn_store), which writes the "
                "Session->turn CONTAINS edges but does NOT run the entity-link pass — "
                "so 0 aboutObject here is the SEEDER's shape, not the writer's",
    })


def _mark_shrunken_denominator(body: dict) -> dict:
    """A graph that RAISED is not a graph that was measured: say so at the arm level."""
    errors = body.get("skipped_on_error") or []
    if errors:
        named = ", ".join(e["graph"] for e in errors[:5])
        more = "…" if len(errors) > 5 else ""
        body["unmeasurable"] = (
            f"{len(errors)} of {body.get('graphs_on_instance')} graphs raised during the "
            f"census ({named}{more}), so the aggregates are a SHRUNKEN denominator — "
            "'0 aboutObject in N of N' is NOT reproducible from this run")
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


def measure_writer() -> dict:
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        return {"skipped": "TORTOISE_DB_URI is not exported — run the lane seam first "
                           "(uri=\"$(uv run python tools/test_lane.py uri)\"; eval \"$uri\")"}
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
            per_session[sid] = {
                k: g.query(q, params={"sid": sid}).result_set[0][0]
                for k, q in _PER_SESSION.items()
            }
        after = _run_queries(g)
        replayed = [sid for sid, c in captures.items()
                    if c.get("extraction_mode") == "replayed"]
        body = {
            "uri_graph": uri.rsplit("/", 1)[-1],
            "sdk": "TortoiseSDK() against TORTOISE_DB_URI",
            "object_mint": {"name": "test/repo#42", "kind": "pm:issue",
                            "advisory": obj.get("kind_layer_routed")
                            or obj.get("kind_redirect")},
            "captures": captures,
            "per_session": per_session,
            "counts": after,
            "counts_before_any_write": before,
            "delta_this_run": _delta(before, after),
            "queries": dict(QUERIES),
            "per_session_queries": _PER_SESSION,
        }
        if replayed:
            # A replay proves nothing about a writer: nothing was written. The numbers
            # that follow are the PRE-EXISTING content of this graph.
            body["unmeasurable"] = (
                "sessions replayed, not captured: " + ", ".join(replayed) +
                " — this graph already held these session ids, so the run wrote nothing "
                "and the counts describe an earlier run. Re-run on a FRESH graph name.")
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
             "turn_occurrences_with_ref": 0}
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
                sid = rec.get("session_id") or rec.get("sessionId") \
                    or f"{f.name}:{stats['records']}"
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--store-graph", default=DEFAULT_STORE_GRAPH)
    ap.add_argument("--live-uri", default=DEFAULT_LIVE_URI)
    ap.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR)
    ap.add_argument("--arms", default="store,writer,live,instrument,corpus",
                    help="comma-separated subset of store,writer,live,instrument,corpus")
    ap.add_argument("--out", default=None, help="receipt JSON path")
    args = ap.parse_args(argv)

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    receipt: dict = {
        "issue": 4211,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code_head": _git_head(),
        "instrument": {
            "path": "tools/measure_4211_session_hierarchy.py",
            "sha256": _sha256(_TOOL_PATH),
            "argv": sys.argv[1:],
            "note": "code_head is the tree this run STARTED from; it may predate the "
                    "commit that carries this file, so the sha256 is what a re-run keys on",
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
    try:
        if "store" in arms:
            receipt["arms"]["store"] = measure_store(args.store, args.store_graph)
        if "writer" in arms:
            receipt["arms"]["writer"] = measure_writer()
        if "live" in arms:
            receipt["arms"]["live"] = measure_live(args.live_uri)
        if "instrument" in arms:
            receipt["arms"]["instrument"] = measure_instrument(args.live_uri)
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
    unmeasurable_arms = {
        arm: body["unmeasurable"]
        for arm, body in receipt["arms"].items()
        if isinstance(body, dict) and body.get("unmeasurable")
    }
    if unmeasurable_arms:
        receipt["unmeasurable"] = {
            "arms": unmeasurable_arms,
            "note": "at least one arm could not measure; where an arm still reports "
                    "numbers they describe PRE-EXISTING graph content, never this "
                    "run's capture",
        }

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
        if inst.get("skipped_on_error"):
            print("  ⛔ graphs that RAISED    :", inst["skipped_on_error"])
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
