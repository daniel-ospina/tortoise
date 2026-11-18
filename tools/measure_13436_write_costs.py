#!/usr/bin/env python
"""#13436 **P7** — the three unowned write-path costs, MEASURED. Diagnosis only.

⛔ THIS TOOL OPTIMISES NOTHING, AND MUST NOT BE TURNED INTO AN OPTIMISATION.
`#13436` names the failure it would be: *"An optimisation off an unmeasured cost is
the regression path."* Each of the three costs below has a recorded decision that
forbids the obvious "fix", and every one of them is a decision this tool deliberately
does not touch:

1. `_nudge_candidates` — a 400-row `:Point` scan returning the FULL `content` of every
   row, paid on every entity create, every edge create and every ingest point. Its only
   product is an ADVISORY field (`nudges`) in the write response.
2. the `#5475` approved-kind gate — a whole-graph approval read
   (`pack_state.graph_installed_namespaces` → `graph_kind_namespaces`: per-property
   scans, `O(nodes)`) paid for every write that NAMES a pack. ⛔ DELETE THE GATE AND YOU
   HAVE A BYPASS: on a `dev`-only graph `objectKind='marketing:contentBrief'` made the
   whole `marketing` pack writable. A cheaper gate is allowed; a weaker one is not
   (`#5475` Option A; `#8977`'s acceptance criterion says so in as many words).
3. `projection._schema_is_current()` — the fail-closed pre-check that `_ensure_indexes`
   is idempotent. ⛔ Do NOT "optimise" it by dropping its `IS NOT NULL` conjuncts or by
   trusting the `point_fts_v2` marker: the docstring at that function records both as
   measured regressions/lost work, and `#5444` is the sanctioned route.

WHAT IS MEASURED, AND ON WHAT
-----------------------------
Everything below is measured against a REAL FalkorDB (the docker lane — `MODULE LIST`'s
version is recorded in the artifact) through the SHIPPED code, never a re-implementation:

* **arm `nudges`** — `TortoiseSDK._nudge_candidates` called directly (its latency and
  its PAYLOAD: rows returned and bytes of `content` moved), plus the SAME door with and
  without the nudge call, in one graph, alternating: the delta is what the advisory
  actually costs the write path. The counterfactual is an instance-attribute patch
  (`sdk._nudge_candidates = lambda *a, **k: []`) — the same object, the same graph, the
  same seeds, so nothing but the nudge differs between the two medians.
* **arm `kindgate`** — the gate's own read is counted, not inferred. `pack_state.
  graph_installed_namespaces` is wrapped with a counter (the gate resolves it through
  `from tortoise.pack_state import graph_installed_namespaces` at CALL time, so the
  module attribute is the seam), and one request per shape reports **scans per
  request** and total ms. A shape that names no pack (`bare`, `core:…`) is the control
  that must show **0** scans — if it does not, the counter is lying and the arm refuses.
* **arm `schema`** — `_schema_is_current()` median-of-N on graphs of growing Point
  count, reporting ms and µs/point beside the return value. The function's own
  docstring already carries a table (1.4 ms @1k → 66.2 ms @300k, and a re-measured
  three-way sweep on 4.20.6); this arm re-measures **the shipped shape** so a reader can
  see whether that table still describes the code in front of them.

WHAT THIS TOOL DOES NOT MEASURE (stated so no reader infers it)
---------------------------------------------------------------
* **The production graph.** Every arm runs on a throwaway `test_measure13436_*` graph
  this tool creates and names in its artifact. Node counts and content LENGTHS are
  ours; the production graph's mix is not sampled, and `#13232`'s own "Unknown 2" — is
  `_nudge_candidates` ms or seconds on production — is answered only for the sizes
  measured here.
* **The embedding model.** Points are seeded without a vector (the keyword-only dev
  shape), so no arm pays `EmbeddingModel.get()`. That is deliberate — the cold load is
  `#13229`'s cost, measured there, and mixing it in would swamp three costs measured in
  milliseconds.
* **Anything downstream of the numbers.** No threshold is proposed, no arm is called
  "fast" or "slow", and the artifact carries no recommendation beyond the decisions it
  cites. The numbers feed `#13103`'s window and `#8977`/`#5444`'s existing proposals.

REFUSALS (a partial arm is refused, never summarised from its survivors)
-----------------------------------------------------------------------
* a seeded population that did not LAND is refused (the count is read back);
* an arm whose control is wrong (a `core:` write paying a scan, or an unarmed gate
  paying none) is refused rather than published;
* `_schema_is_current()` returning `False` on an arm is REFUSED: a `False` means the
  probe fell through to the full sweep, so timing it would report the sweep's cost as
  the probe's.

USAGE
-----
    export TORTOISE_DB_URI='docker://:@127.0.0.1:16402/tortoise_test_matrix'   # a lane
    uv run python tools/measure_13436_write_costs.py                 # all arms
    uv run python tools/measure_13436_write_costs.py --arm nudges
    uv run python tools/measure_13436_write_costs.py --json docs/evidence/13436/write-path-costs.json

Exit codes: 0 every requested arm measured · 3 an arm was refused (reason printed) ·
2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

#: A real-shaped claim, ~220 chars — the length a captured `:Point` carries on the
#: graphs this repo writes (the seeded `10,457 keys / 21,110 nodes` dogfood shape is
#: Points of a line or two, not one word).
CONTENT_TEMPLATE = (
    "the write door refused the record because the fold had already committed the "
    "status change and the journal line for it was appended first, so the refusal "
    "left no node behind and the caller saw one error instead of a partial write "
    "(round {i}, measured on a graph whose point population was {i} rows deep)"
)

#: The nudge probe text: ordinary vocabulary, so the token overlap is realistic rather
#: than either maximal (every token shared) or zero (no candidates scored at all).
NUDGE_TEXT = (
    "the record must hold and index the label before the graph copy is retired, and "
    "the fold must keep the status change it already committed"
)

#: The EXACT shipped statement, copied from `sdk._nudge_candidates` — copied rather than
#: imported because the payload measurement is about the statement's shape (which
#: columns it returns), and a `_nudge_candidates` refactor must red this tool rather
#: than silently change what it measures. The arm asserts the call site's text matches
#: this string (see `_assert_statement_matches_source`).
NUDGE_SQL = (
    "MATCH (n:Point) WHERE NOT n.id IN $ex "
    "RETURN n.id, n.content, n.name, n.label, n.is_operator LIMIT 400"
)


class ArmRefused(RuntimeError):
    """An arm could not be measured as posed. Never published as a number."""


# ── small helpers ───────────────────────────────────────────────────────────


def _median_ms(samples: list[float]) -> dict:
    return {
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "n": len(samples),
    }


def _time_call(fn, repeats: int, warmups: int = 2) -> dict:
    for _ in range(warmups):
        fn()
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return _median_ms(samples)


def _content(i: int) -> str:
    return CONTENT_TEMPLATE.format(i=i)


def _loadavg() -> list[float] | str:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:  # pragma: no cover - platform without loadavg
        return "unavailable"


def _engine_version(proj) -> str:
    """The FalkorDB `graph` module version, read the way the guard reads it.

    `MODULE LIST` is a REDIS command, not Cypher — issuing it through `g.query`
    returns a parse error (measured), which is why this goes through the client's
    `execute_command` and decodes the module's own `ver` integer (`ver 42006` =
    4.20.6, the encoding `tortoise/retry.py` documents). Every failure is
    DISCLOSED rather than fatal: an artifact that names no engine version is
    weaker, not wrong.
    """
    try:
        from tortoise.cypher_guard import _graph_module_version

        client = getattr(getattr(proj, "db", None), "connection", None)
        found = _graph_module_version(client) if client is not None else None
        if found:
            major, minor, patch = found
            return f"graph ver {major * 10000 + minor * 100 + patch} ({major}.{minor}.{patch})"
        return "unavailable (module list answered no graph module)"
    except Exception as e:  # noqa: BLE001 - disclose, never fail the artifact
        return f"unavailable ({e.__class__.__name__}: {e})"


def _assert_statement_matches_source() -> None:
    """The measured statement must be the SHIPPED one (a copy that drifted lies)."""
    src = Path(__file__).resolve().parent.parent / "tortoise" / "sdk.py"
    text = src.read_text(encoding="utf-8", errors="replace")

    def _norm(s: str) -> str:
        # The shipped statement is a concatenation of adjacent literals over two
        # lines, so the SOURCE carries quotes and newlines the VALUE does not.
        # Drop the quote characters and collapse whitespace on both sides — the
        # comparison is about the statement's text and its column list, not about
        # how the literal happens to be laid out.
        return " ".join(s.replace('"', " ").split())

    if _norm(NUDGE_SQL) not in _norm(text):
        raise ArmRefused(
            "`_nudge_candidates`' statement is not the one this tool measures "
            f"({_norm(NUDGE_SQL)!r} absent from tortoise/sdk.py) — re-read the call "
            "site and update both the statement and this tool's account of it.")


def _seed_points(proj, n: int, *, prefix: str, content: str | None = None) -> None:
    """Seed `n` `:Point` rows through ONE `UNWIND` per 500 (fast, verifiable)."""
    batch = 500
    for start in range(0, n, batch):
        rows = [
            {
                "id": f"{prefix}-{i:06d}",
                "content": content if content is not None else _content(i),
                "label": "statement",
                "name": f"seed claim {i}",
            }
            for i in range(start, min(start + batch, n))
        ]
        proj.g.query(
            "UNWIND $rows AS r CREATE (n:Point {id: r.id, content: r.content, "
            "label: r.label, name: r.name})",
            params={"rows": rows},
        )


def _assert_seeded(proj, n: int, prefix: str) -> None:
    got = proj.g.query(
        "MATCH (n:Point) WHERE n.id STARTS WITH $p RETURN count(n)",
        params={"p": prefix + "-"},
    ).result_set[0][0]
    if int(got) != n:
        raise ArmRefused(
            f"seeded population did not land: expected {n} `{prefix}-*` Points, "
            f"read {got} back. A measurement over an unknown population is not one.")


def _fresh_sdk(graph_name: str):
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK(graph_name=graph_name)


def _graph_name(tag: str, n: int) -> str:
    # `test_` prefix: nothing in this tool may address a production-named graph.
    return f"test_measure13436_{tag}_{n}_{os.getpid()}"


# ── arm 1: `_nudge_candidates` ──────────────────────────────────────────────


def arm_nudges(sizes: list[int], reps: int, content_lengths: list[int]) -> dict:
    _assert_statement_matches_source()
    out = {"arm": "nudges", "cost": "400-row :Point scan returning full content",
           "by_size": [], "by_content_length": []}
    for n in sizes:
        sdk = _fresh_sdk(_graph_name("nudge", n))
        proj = sdk._get_proj()
        prefix = f"nudge{n}"
        _seed_points(proj, n, prefix=prefix)
        _assert_seeded(proj, n, prefix)

        # The PAYLOAD: what the statement moves, read exactly as the call reads it.
        rows = proj.g.query(NUDGE_SQL, params={"ex": []}).result_set
        content_bytes = sum(len(str(r[1] or "")) for r in rows)
        name_bytes = sum(len(str(r[2] or "")) for r in rows)
        scanned = len(rows)

        call = _time_call(lambda: sdk._nudge_candidates(NUDGE_TEXT, limit=3), reps)
        produced = sdk._nudge_candidates(NUDGE_TEXT, limit=3)

        # THE DOOR DELTA — same graph, same seeds, only the nudge differs.
        def door(tag: str):
            made = {"i": 0}

            def _one():
                made["i"] += 1
                sdk.create_entity(type="object", name=f"{tag} {made['i']}",
                                  objectKind="core:other")

            return _one

        with_nudge = _time_call(door("withnudge"), reps, warmups=1)
        original = sdk._nudge_candidates
        sdk._nudge_candidates = lambda *a, **k: []  # type: ignore[assignment]
        try:
            without_nudge = _time_call(door("withoutnudge"), reps, warmups=1)
        finally:
            sdk._nudge_candidates = original  # type: ignore[assignment]

        out["by_size"].append({
            "points": n,
            "scan": {
                "rows_returned": scanned,
                "row_cap": 400,
                "content_bytes_returned": content_bytes,
                "name_bytes_returned": name_bytes,
                "content_bytes_per_row": round(content_bytes / scanned, 1) if scanned else 0,
            },
            "nudge_call": call,
            "nudges_produced": len(produced),
            "object_create_ms": {
                "with_nudge": with_nudge,
                "without_nudge": without_nudge,
                "delta_ms": round(with_nudge["median_ms"] - without_nudge["median_ms"], 3),
            },
        })
        print(f"  nudges @ {n:>6} points: scan {scanned} rows / "
              f"{content_bytes/1024:.1f} KiB content, "
              f"call {call['median_ms']} ms, door delta "
              f"{out['by_size'][-1]['object_create_ms']['delta_ms']} ms", flush=True)

    # THE CONTENT AXIS — the row cap is CONSTANT (400) while the bytes it moves are
    # not, so this is the axis that decides whether the cost is the label scan or
    # the payload. One graph per length, same Point count, only the content differs.
    for chars in content_lengths:
        n = sizes[0] if sizes else 1000
        sdk = _fresh_sdk(_graph_name(f"nudgec{chars}", n))
        proj = sdk._get_proj()
        prefix = f"nudgec{chars}"
        body = ("x" * max(1, chars - 24)) + " tail marker text"
        _seed_points(proj, n, prefix=prefix, content=body[:chars])
        _assert_seeded(proj, n, prefix)
        rows = proj.g.query(NUDGE_SQL, params={"ex": []}).result_set
        content_bytes = sum(len(str(r[1] or "")) for r in rows)
        call = _time_call(lambda: sdk._nudge_candidates(NUDGE_TEXT, limit=3), reps)
        out["by_content_length"].append({
            "points": n,
            "content_chars_per_row": chars,
            "rows_returned": len(rows),
            "content_bytes_returned": content_bytes,
            "nudge_call": call,
        })
        print(f"  nudges content {chars:>5} chars/row: {content_bytes/1024:7.1f} KiB, "
              f"call {call['median_ms']} ms", flush=True)
    return out


# ── arm 2: the #5475 approved-kind gate ─────────────────────────────────────


def arm_kindgate(n: int, reps: int) -> dict:
    from tortoise import pack_state

    sdk = _fresh_sdk(_graph_name("kindgate", n))
    proj = sdk._get_proj()
    prefix = f"gate{n}"
    _seed_points(proj, n, prefix=prefix)
    _assert_seeded(proj, n, prefix)

    activated = pack_state.ensure_tenant_packs(sdk, starter=["dev"])
    if not activated:
        raise ArmRefused("could not activate the `dev` pack — the gate would pay nothing")
    approved = pack_state.graph_installed_namespaces(sdk)
    if not approved:
        raise ArmRefused(f"approval set is {approved!r}: the gate is not armed on this graph")

    # ⛔ COUNT the gate's own read. The gate resolves it through
    # `from tortoise.pack_state import graph_installed_namespaces` INSIDE the call, so
    # the module attribute is the seam — a patch on the SDK would be a patch on a name
    # nobody reads.
    calls = {"n": 0}
    real = pack_state.graph_installed_namespaces

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    single_scan = _time_call(lambda: real(sdk), reps)

    shapes = [
        ("point_core", "control — names no pack", lambda i: sdk.create_point(
            "core:Project", f"a core point {i}"), 0),
        ("point_bare", "control — names no pack", lambda i: sdk.create_point(
            "statement", f"a bare-kind point {i}"), 0),
        ("point_ns", "names a pack", lambda i: sdk.create_point(
            "dev:epic", f"a dev point {i}"), 1),
        ("point_ns_in_props", "names a pack through the props passthrough",
         lambda i: sdk.create_point("statement", f"a dev point via props {i}",
                                    pointKind="dev:epic"), 1),
        ("object_ns", "names a pack", lambda i: sdk.create_entity(
            type="object", name=f"a dev object {i}", objectKind="dev:epic"), 1),
        # ⛔ THE ONE SHAPE THAT PAYS TWICE — and it is the SPELLING, not the
        # namespace: a write that names the SAME pack kind through two spellings
        # (`create_point`'s positional `kind` plus the props passthrough, or the
        # camel/snake pair the fold accepts) is gated once per SPELLING, so the
        # whole-graph read is paid twice in one request. Pinned here so the arm
        # reports the count rather than leaving two as folklore.
        ("point_ns_twice", "SAME pack named twice (positional + props)",
         lambda i: sdk.create_point("dev:epic", f"a doubled dev point {i}",
                                    pointKind="dev:epic"), 2),
        ("object_ns_twice", "SAME pack named twice (camel + snake)",
         lambda i: sdk.create_entity(
             type="object", name=f"a doubled dev object {i}",
             objectKind="dev:epic", object_kind="dev:epic"), 2),
    ]

    rows = []
    pack_state.graph_installed_namespaces = counted  # type: ignore[assignment]
    try:
        for shape, note, fn, expect in shapes:
            i = {"n": 0}
            samples = []
            scans_per_request = []
            for _ in range(reps + 1):  # one warmup, discarded
                i["n"] += 1
                before = calls["n"]
                t0 = time.perf_counter()
                fn(i["n"])
                dt = (time.perf_counter() - t0) * 1000.0
                scans_per_request.append(calls["n"] - before)
                samples.append(dt)
            samples = samples[1:]  # drop warmup
            scans = scans_per_request[1:]
            if len(set(scans)) != 1:
                raise ArmRefused(
                    f"shape {shape!r} paid a VARYING number of gate reads {scans} — "
                    "one request cannot pay two different costs; re-read the arm")
            rows.append({
                "shape": shape,
                "note": note,
                "expected_gate_reads": expect,
                "gate_reads_per_request": scans[0],
                "request_ms": _median_ms(samples),
                "gate_ms_per_request": round(scans[0] * single_scan["median_ms"], 3),
            })
            print(f"  gate {shape:<20} reads/request={scans[0]}  "
                  f"request {rows[-1]['request_ms']['median_ms']} ms  "
                  f"(gate {rows[-1]['gate_ms_per_request']} ms)", flush=True)
    finally:
        pack_state.graph_installed_namespaces = real  # type: ignore[assignment]

    # The expectations must hold, or the counter is lying. A control that pays a
    # read, a namespaced write that pays none, and a two-spelling write that pays
    # one are each REFUSED rather than published.
    for row in rows:
        if row["gate_reads_per_request"] != row["expected_gate_reads"]:
            raise ArmRefused(
                f"{row['shape']} paid {row['gate_reads_per_request']} gate reads, "
                f"expected {row['expected_gate_reads']} ({row['note']}) — either the "
                "gate moved or the counter measures something else; re-read the arm")

    return {
        "arm": "kindgate",
        "cost": "whole-graph approval scan (#5475 gate), per write that names a pack",
        "points": n,
        "approved_set": sorted(approved),
        "single_gate_read": single_scan,
        "by_shape": rows,
        # ⛔ A COMPOSITION OF MEASURED PARTS, NOT AN END-TO-END HTTP MEASUREMENT —
        # the per-request count for the HOSTED commit door. Its own comment
        # (`hosted_api.py:23943-23946`) says a second `graph_installed_namespaces`
        # in the pre-check would be "a second whole-graph scan AND a second
        # answer", so the pre-check reads the set ONCE per request and the write
        # doors underneath then pay their own read per namespaced entity. The
        # parts are measured here; the sum is arithmetic on them and is labelled
        # as such so no reader takes it for a request-level sample.
        "hosted_commit_composition": {
            "rule": "1 pre-check read per request + 1 read per namespaced entity written",
            "pre_check_reads": 1,
            "per_entity_writes": rows[-1]["gate_reads_per_request"] if rows else None,
            "pre_check_site": "tortoise/hosted_api.py:23946 (deliberately NOT rescanned at :23976)",
            "per_entity_site": "tortoise/sdk.py::_create_entity → _refuse_unapproved_kinds_in_props",
            "single_entity_commit_total_reads": 1 + (rows[-1]["gate_reads_per_request"] if rows else 0),
            "measured_as": "composition of measured parts — not an HTTP sample",
        },
    }


# ── arm 3: `_schema_is_current()` ───────────────────────────────────────────


def arm_schema(sizes: list[int], reps: int) -> dict:
    out = {"arm": "schema", "cost": "per-construction _schema_is_current() probe", "by_size": []}
    for n in sizes:
        sdk = _fresh_sdk(_graph_name("schema", n))
        proj = sdk._get_proj()
        prefix = f"schema{n}"
        _seed_points(proj, n, prefix=prefix)
        _assert_seeded(proj, n, prefix)
        verdict = proj._schema_is_current()
        if verdict is not True:
            raise ArmRefused(
                f"`_schema_is_current()` returned {verdict!r} at {n} Points — a False "
                "means the probe fell through to the full sweep, so timing it would "
                "report the sweep's cost as the probe's")
        measured = _time_call(lambda: proj._schema_is_current(), reps)
        out["by_size"].append({
            "points": n,
            "verdict": verdict,
            "probe_ms": measured,
            "us_per_point": round(measured["median_ms"] * 1000.0 / n, 3),
        })
        print(f"  schema @ {n:>6} points: {measured['median_ms']} ms "
              f"({out['by_size'][-1]['us_per_point']} µs/point)", flush=True)
    return out


# ── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", action="append", choices=["nudges", "kindgate", "schema"],
                    help="measure only this arm (repeatable); default: all three")
    ap.add_argument("--nudge-sizes", default="1000,5000",
                    help="Point populations for the nudges arm (default 1000,5000)")
    ap.add_argument("--nudge-content-sizes", default="60,300,1200",
                    help="content chars/row for the nudges payload axis (default 60,300,1200)")
    ap.add_argument("--schema-sizes", default="1000,5000,20000",
                    help="Point populations for the schema arm (default 1000,5000,20000)")
    ap.add_argument("--gate-points", type=int, default=5000,
                    help="Point population for the gate arm (default 5000)")
    ap.add_argument("--reps", type=int, default=7, help="samples per measurement (default 7)")
    ap.add_argument("--json", default="", help="write the artifact here")
    args = ap.parse_args(argv)

    arms = args.arm or ["nudges", "kindgate", "schema"]
    nudge_sizes = [int(x) for x in args.nudge_sizes.split(",") if x.strip()]
    nudge_content_sizes = [int(x) for x in args.nudge_content_sizes.split(",") if x.strip()]
    schema_sizes = [int(x) for x in args.schema_sizes.split(",") if x.strip()]

    artifact: dict = {
        "issue": 13436,
        "arm_set": "P7 — write-path costs, diagnosis only (no optimisation)",
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "box": {
            "loadavg_at_start": _loadavg(),
            "python": sys.version.split()[0],
            "db_uri_env": os.environ.get("TORTOISE_DB_URI", "<unset>"),
        },
        "arms": {},
    }

    # One throwaway graph is enough to read the engine version.
    probe = _fresh_sdk(_graph_name("version", 0))
    artifact["box"]["engine"] = _engine_version(probe._get_proj())

    print(f"[13436 P7] engine: {artifact['box']['engine']}  load {_loadavg()}", flush=True)
    try:
        if "nudges" in arms:
            print("[arm] nudges — 400-row :Point scan returning full content", flush=True)
            artifact["arms"]["nudges"] = arm_nudges(
                nudge_sizes, args.reps, nudge_content_sizes)
        if "kindgate" in arms:
            print("[arm] kindgate — #5475 whole-graph approval scan", flush=True)
            artifact["arms"]["kindgate"] = arm_kindgate(args.gate_points, args.reps)
        if "schema" in arms:
            print("[arm] schema — per-construction _schema_is_current()", flush=True)
            artifact["arms"]["schema"] = arm_schema(schema_sizes, args.reps)
    except ArmRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3

    artifact["box"]["loadavg_at_end"] = _loadavg()

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        print(f"artifact: {path}")
    else:
        print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
