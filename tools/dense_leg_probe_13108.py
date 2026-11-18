#!/usr/bin/env python
"""The dense leg's MECHANISM and LATENCY, per label, at rest and under load (#13108).

WHY THIS EXISTS
---------------
`#13108` measured that **45 % of eval windows lose the dense leg** on a loaded box
(`window_modes {hybrid: 2371, keyword_only: 1973}`) and that recall collapses with it.
The *labelling* already exists and is correct (`#2952`'s
:func:`tortoise.search_engine.declared_degraded_read`, `#4999`'s `mechanism` key), so the
open question is not "did the read say it degraded" — it is **why the leg runs out of
budget**, and the two candidate answers have OPPOSITE fixes:

* **the unindexed scan** — `entity_type='object'` reads `:Object`, which carries no vector
  index (`tools/dod_5090_probe.py` row 2: `indexed: ['Point']` only), so every object-ish
  dense read is a full scan that first pays a FAILING index round trip. If this is the
  cause, the fix is an index, and that is `STORAGE-ARCHITECTURE.md` §14.3 **V3** — an OWNER
  decision `#11901` closed as *"not earned"* on a 390 s sweep. **This tool measures; it creates
  no VECTOR index on `:Object`, `:Subject`, `:Event` or `:Source`** — not by a flag, not by an
  argument. ⛔ **"VECTOR" is load-bearing** (`#13121` round 13): what the projection's own
  `_ensure_indexes()` creates on a fresh graph is (a) the `:Point` VECTOR index the product
  already ships — the one thing this tool creates that it does not own, and what makes the
  `:Point` arm a control (see "THE ONE THING THE SEED DOES NOT DO") — and (b) the ordinary RANGE
  and FULL-TEXT indexes those labels have always carried. Neither (b) nor a plain "it indexes
  nothing" is what V3 asks about; creating the VECTOR index on the served-but-unindexed labels
  WOULD reverse a recorded decision on a hunch.
* **budget-only** — the leg is merely slow, and the fix is the budget or a bounded retry.

⛔ **A bug that only appears under contention cannot be reproduced on an idle box.** The
dispatch that produced this file measured on a box at **load 124**. So the tool reports
EVERY arm with the machine's own load average printed beside it, and refuses to let a
"rest" number and a "loaded" number share a table without saying which is which.

WHAT IS MEASURED, AND WHAT ISN'T
--------------------------------
MEASURED: the shipped read function, :func:`tortoise.search_engine.run_vector_query`, with
``is_embedded=False`` (the docker/server path the product uses), a real query vector, a real
FalkorDB, and ``leg_trace`` on — so every sample carries the **mechanism actually taken**
(``index`` | ``scan`` | ``scan_fallback``, `#4999`) and the reason it stopped.

NOT MEASURED, and deliberately: the query EMBEDDING. ``sdk.tortoise_fts_query`` encodes the
query on the caller's thread **before** :func:`tortoise.search_engine.degradation_chain`
(``sdk.py``: ``query_vec = model.encode(...)`` precedes the chain), so encoder cost is
outside the 500 ms cap by construction and cannot be what trips it. This tool passes a
vector that is already in the corpus, which removes the encoder from the sample entirely —
that is the point, not a shortcut: it isolates the STORE half of the leg.

AN ARM THAT DOES NOT COMPLETE IS REFUSED, NOT PUBLISHED SHORT
-------------------------------------------------------------
A sample the shipped read reports under a mechanism this tool has never seen
(:data:`MECHANISMS`), or any other failure that escapes a worker, REFUSES the
whole arm (:class:`UnknownMechanism` -> :func:`arm_integrity`). It is never
summarised from the survivors: ``over 500ms: 37/100`` on an arm that ran 100
reads is a wrong number that reads as a measured one, which is the failure
class this file's ``UNMEASURED``-is-never-``0`` rule exists to refuse.

THE ONE THING THE SEED DOES NOT DO
----------------------------------
Both labels are seeded with **the same names and the same vectors** (real names harvested by
`tools/candidate_recall_11901.py`'s own ``harvest``/``build_population``, imported and never
re-implemented), so the two arms differ in EXACTLY one thing: whether the engine holds a
vector index for the label. The `:Object` seed goes through the REAL projection
(``_upsert_object``); the `:Point` mirror is written raw from the object's own stored
embedding, because its only job is to be the indexed control at the same volume.

⛔ ON A FRESH GRAPH THIS TOOL CREATES ONE THING IT DOES NOT OWN: the `:Point` VECTOR index,
via the projection's own `_ensure_indexes()`. That is the index the product already ships
(`#4997`: ``indexed: ['Point']``) and it is what makes the control a control. **No index is
created on `:Object`, `:Subject`, `:Event` or `:Source` — not by a flag, not by an argument.**

USAGE
-----
::

    uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
    uv run python tools/dense_leg_probe_13108.py --reads 100 --concurrency 1
    uv run python tools/dense_leg_probe_13108.py --reads 100 --concurrency 16,64 --out /tmp/loaded.json

``--concurrency`` is repeatable/comma-separated and is the tool's own load lever: it opens
that many real concurrent dense reads against the one graph, which is what a capture fleet
does to it. ``--external-load N`` additionally runs N real embedder processes (the capture
write path's own work) for the duration of the arm, for the case where the contention that
matters is CPU rather than the store.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first, and the operator would get an
# unattributed traceback instead of this sentence (the `tools/` entry-point
# contract; `tests/test_entry_point_python_guard.py` pins it for the corpus).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/dense_leg_probe_13108.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/dense_leg_probe_13108.py`"
    )

import argparse
import collections
import hashlib
import json
import os
import statistics
import subprocess
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import-path bootstrap
    sys.path.insert(0, str(REPO_ROOT))

#: The production budget this issue is about. ⛔ `search_engine.py:3586` is the VECTOR leg's own
#: `timeout_ms` default (`VECTOR_LEG_BUDGET_MS` = 500) and `:5136` is `degradation_chain`'s
#: `runner_timeout`; `:2886` is `run_fts_query`'s (the LEXICAL leg's) and `:4970` is a comment line
#: about `#4210`/`#3019` — an earlier revision cited both (`#13121` round 8 F4).
#: The collector's own `as_completed(timeout=0.5)` is the second budget (`#8450`).
DEFAULT_TIMEOUT_MS = 500

#: entity_type -> the label `security.entity_label` resolves it to (the census's
#: own translation, restated here only for the probe's printouts).
LABEL_BY_ENTITY = {"object": "Object", "point": "Point", "subject": "Subject",
                   "event": "Event", "source": "Source", "document": "Source"}

#: `#4999`'s mechanism vocabulary, restated here as the CLOSED set a sample may
#: report — so a mechanism this tool has never seen REFUSES the arm
#: (:class:`UnknownMechanism`) instead of joining a silent bucket.
MECHANISMS = ("index", "scan", "scan_fallback", "pgvector")


class UnknownMechanism(RuntimeError):
    """A sample reported a mechanism outside `#4999`'s declared vocabulary.

    ⛔ Deliberately NOT a `SystemExit` raised from inside the worker: CPython's
    default :func:`threading.excepthook` **silently ignores** an uncaught
    ``SystemExit`` raised by a thread (``docs.python.org/3/library/threading.html``;
    reproduced on 3.12.3 — no message, no traceback, the process continues). A
    guard written that way is neither loud nor fatal: the thread dies quietly,
    every sample it had already collected is discarded with it, and the arm is
    published from the survivors with nothing marking it truncated — the exact
    failure mode this instrument exists to prevent (`#13121` review F2).

    This class is caught by :func:`measure` on the worker's own thread, recorded,
    and turned into a refused arm (:func:`arm_integrity`).
    """


# ── statistics (pure, and pinned by the tests) ──────────────────────────────

def percentile(values: list[float], pct: float) -> float | None:
    """The NEAREST-INDEX percentile: ``ordered[round((n-1) * pct/100)]``.

    ``None`` for an empty sample, never ``0``. An empty sample and a
    zero-millisecond sample are different facts (the same distinction
    ``tools/dod_5090_probe.py``'s census draws between ``UNMEASURED`` and ``0``),
    and a percentile that answers ``0`` for "nothing ran" is exactly the shape
    that reads as measured without being measured.

    ⛔ **This is NOT the nearest-RANK estimator** (``ordered[ceil(n*pct/100)-1]``,
    the ``PERCENTILE.INC`` / "nearest-rank" convention), and ``#13121`` round 14
    deleted a docstring that said it was. The two agree on most samples and part
    ways at the TAIL of a short arm: on the 60-sample ``:Object`` c=1 under-load
    arm this answers ``ordered[58] = 634.50 ms`` for ``p99``, where nearest-rank
    answers ``max = 644.97 ms``. Both are published — ``max`` sits beside ``p99``
    in every ``latency_ms`` block — so **quote ``max`` when the tail is the
    claim**, never ``p99`` alone.

    ⛔ **Why the convention is not swapped to nearest-rank here:** every committed
    artifact under ``docs/research/2026-11-17-13108-dense-leg/`` carries a
    ``latency_ms`` block computed by THIS formula, and re-deriving those blocks
    needs the engine (they are not regenerable offline). Swapping the estimator
    would leave each artifact's ``p99`` disagreeing with the tool that wrote it —
    precisely the claim/artifact mismatch class ``#13121`` rounds 4–13 removed. So
    the honest move is to NAME what this computes, pin it with a discriminating
    row, and publish ``max`` beside it.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = max(0, min(len(ordered) - 1,
                   round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[k]


def summarise(samples: list[dict]) -> dict:
    """One arm's latency + mechanism + outcome distribution.

    ``over_budget`` counts samples at or beyond ``timeout_ms`` — the ones the
    collector's deadline would abandon (``reason="timeout"`` on the trace) or the
    driver would kill (``query_failed``). Both are the same user-visible outcome:
    the read proceeds without the dense leg.
    """
    lat = [s["elapsed_ms"] for s in samples]
    mech = collections.Counter(s.get("mechanism") for s in samples)
    reason = collections.Counter(s.get("reason") for s in samples)
    return {
        "n": len(samples),
        "latency_ms": {
            "p50": percentile(lat, 50),
            "p95": percentile(lat, 95),
            "p99": percentile(lat, 99),
            "max": max(lat) if lat else None,
            "mean": round(statistics.fmean(lat), 2) if lat else None,
        },
        "mechanism": {str(k): v for k, v in sorted(
            mech.items(), key=lambda kv: str(kv[0]))},
        "reason": {str(k): v for k, v in sorted(
            reason.items(), key=lambda kv: str(kv[0]))},
        "count": {
            # #13121 round 7 (F5): the three counters below follow the SAME
            # None-not-0 rule as every latency cell — `0 over budget` on an arm
            # that took no samples is a measured-looking zero, and
            # `windows-8000-under-load-timeout.json` carries exactly that shape
            # (`n: 0`, `over_budget: 0`, `degraded: 0`, `zero_rows: 0`).
            "zero_rows": (sum(1 for s in samples if not s.get("rows"))
                          if samples else None),
            "rows_mean": (round(statistics.fmean(
                [s.get("rows", 0) for s in samples]), 2) if samples else None),
        },
        "over_budget": (sum(1 for s in samples
                            if s["elapsed_ms"] >= s["timeout_ms"])
                        if samples else None),
        "degraded": (sum(1 for s in samples if s.get("degraded"))
                     if samples else None),
    }


def arm_integrity(entity_type: str, concurrency: int, reads: int,
                  arrived: int, failures: list[str]) -> str | None:
    """The refusal message for an arm that did not COMPLETE, else ``None``.

    ⛔ An incomplete arm is not a short measurement, it is NO measurement: the
    samples a dead worker had already collected are discarded with it
    (:func:`measure`'s worker extends the shared list only once its loop is
    done), so summarising the survivors would publish e.g. ``over 500ms: 0/0``
    — or ``37/100`` — for an arm that ran, with nothing marking it truncated.

    Pure, so the rule is pinned by the tests without a thread.
    """
    if not failures and arrived == reads:
        return None
    detail = f"{arrived}/{reads} reads arrived"
    if failures:
        detail += f"; {len(failures)} worker(s) raised — first: {failures[0]}"
    return (f"REFUSED: the {entity_type}@{concurrency} arm did not complete — "
            f"{detail}. A partial sample is not a measurement (the same rule "
            "this module applies to an empty one); re-run the arm rather than "
            "quoting it.")


# ── seeding ─────────────────────────────────────────────────────────────────

def _import_instrument():
    """`#11901`'s population definitions, imported — never re-implemented."""
    from tools.candidate_recall_11901 import build_population, harvest
    return harvest, build_population


def seed(sdk, population: int, sessions_dir: str) -> dict:
    """Write the SAME ``population`` names + vectors to ``:Object`` (unindexed) and
    ``:Point`` (indexed), and report what landed.

    Returns ``{"names": [...], "objects": n, "points": n, "vectors": n,
    "harvest_stats": {...}}``.

    ``harvest_stats`` is ``harvest``'s OWN input accounting (``files``,
    ``lines``, ``parse_errors``, ``records_without_text``). It is carried into
    the report because the alternative is an artifact that cannot distinguish
    "the corpus lost names" from "the harness stopped parsing records" — the
    distinction `tools/candidate_recall_11901.py::harvest` documents on its own
    ``stats`` argument and drops both records silently without.
    """
    harvest, build_population = _import_instrument()
    sessions = Path(os.path.expanduser(sessions_dir))
    harvest_stats: dict = {}
    harvested = harvest(sessions, harvest_stats)
    pop = build_population(harvested, population)
    if not pop:
        raise SystemExit("REFUSED: the harvest produced no names")

    proj = sdk._get_proj()
    # The real write path for the object arm: real property shape, real embedding,
    # through the EMBED gate (#5008) exactly as production writes it.
    for i, row in enumerate(pop):
        row["id"] = f"obj-13108{i:06d}"
        proj._upsert_object({"id": row["id"], "name": row["name"],
                             "object_kind": "core:other"})

    # The indexed control is a MIRROR of the same rows: same id suffix, same name,
    # same vector. `_ensure_indexes()` (run by the projection's __init__) is what
    # gives :Point its HNSW index — the ONE the product already ships (#4997).
    rows = proj.g.query(
        "MATCH (o:Object) WHERE o.id STARTS WITH 'obj-13108' "
        "RETURN o.id, o.name, o.embedding", params={}).result_set
    written = 0
    vectors = 0
    batch: list[dict] = []
    for oid, name, emb in rows:
        if emb is None:
            continue
        vectors += 1
        batch.append({"id": "pt-" + oid[4:], "name": name, "e": list(emb)})
        if len(batch) >= 500:
            written += _write_point_batch(proj.g, batch)
            batch = []
    if batch:
        written += _write_point_batch(proj.g, batch)
    return {"names": [r["name"] for r in pop], "objects": len(rows),
            "points": written, "vectors": vectors,
            "harvest_stats": dict(harvest_stats)}


def _write_point_batch(graph, batch: list[dict]) -> int:
    """``UNWIND`` the mirror rows — one round trip per 500, not per row."""
    graph.query(
        "UNWIND $rows AS row MERGE (p:Point {id: row.id}) "
        "SET p.name = row.name, p.embedding = vecf32(row.e)",
        params={"rows": batch})
    return len(batch)


def probe_index_attempt(graph, query_vec, label: str, limit: int,
                        timeout_ms: int) -> dict:
    """What a bare ``queryNodes`` on ``label`` costs — tells ``scan_fallback`` apart.

    ``run_vector_query``'s ``scan_fallback`` mechanism says an index attempt FAILED
    and the read fell back to a scan on the SAME call (`#4999`), but it does not say
    WHICH failure, and on this engine the two are different facts:

    * ``no-index`` — the label carries no vector index at all (``Object``). Every
      read of that label pays this round trip and then a full scan, for ever.
    * ``signature`` — the engine rejected the argument ORDER (`#1359`), which the
      shipped leg retries with the other form; it is a per-engine constant, not
      per-label, and it says nothing about an index.

    Getting these two confused is how "the index is missing" becomes "the driver
    call is wrong". Both signatures are tried in the shipped order and every
    attempt is reported. ⚠️ **This engine does NOT distinguish them by message**:
    an unindexed label and a wrong argument order both come back as
    ``Invalid arguments for procedure 'db.idx.vector.queryNodes'`` (measured on
    ``falkordb/falkordb-server:v4.20.6`` — the pin ``tools/test_lane.py``
    launches; ⛔ the committed ``#13108`` artifacts do **not** record a server
    version, which is why ``--out`` now writes ``engine_version`` from
    :func:`engine_version` and why (``#13121`` round 14) this sentence no longer
    cites them for it). The distinguishing evidence is
    therefore NOT the text but the census (:func:`label_census`): the same call
    answers on a label the catalogue holds a VECTOR index for, and is refused on
    one it does not. That is why the two are reported side by side and this
    function refuses to infer a cause from the message alone.
    """
    forms = {
        "A": (f"CALL db.idx.vector.queryNodes('{label}', 'embedding', "
              "$query_vec, $limit) YIELD node RETURN node.id LIMIT $limit"),
        "B": (f"CALL db.idx.vector.queryNodes('{label}', 'embedding', "
              "$limit, vecf32($query_vec)) YIELD node RETURN node.id LIMIT $limit"),
    }
    start = time.monotonic()
    attempts: list[dict] = []
    outcome = "ok"
    answered: str | None = None
    aborted_reason: str | None = None
    aborted_at: str | None = None
    for name, cypher in forms.items():
        a0 = time.monotonic()
        try:
            graph.query(cypher, params={"query_vec": query_vec,
                                        "limit": limit}, timeout=timeout_ms)
        except Exception as e:  # a refusal is a RESULT here
            msg = f"{type(e).__name__}: {e}"
            attempts.append({"signature": name, "outcome": "refused",
                             "elapsed_ms": round((time.monotonic() - a0) * 1000.0, 2),
                             "raised": msg[:300]})
            low = msg.lower()
            if "invalid arguments" in low or "not registered" in low \
                    or "unknown procedure" in low or "type mismatch" in low:
                continue
            # ⛔ NOT a signature refusal: this exception ended the sequence with
            # the OTHER form never tried, so this run measured ONE signature, not
            # both. The likely trigger is the probe query ITSELF hitting
            # ``--timeout-ms`` on a loaded box — the very condition this tool is
            # built to study. Reported under its own outcome (#13121 review F3),
            # with the reason, so the record cannot contradict its own attempts
            # array.
            aborted_at = name
            aborted_reason = (f"signature {name} raised {msg[:300]} — the "
                              "remaining signature(s) were never tried")
            break
        attempts.append({"signature": name, "outcome": "ok",
                         "elapsed_ms": round((time.monotonic() - a0) * 1000.0, 2),
                         "raised": None})
        outcome, answered = "ok", name
        break
    if answered is None:
        if aborted_at is not None:
            outcome = f"aborted_at_signature_{aborted_at.lower()}"
        else:
            # ⛔ NOT named a cause: message-wise this engine says the same thing
            # for an unindexed label as for a bad argument order (see the
            # docstring). The census, printed beside this record, is what
            # separates them.
            outcome = "refused-both-signatures"
    return {"label": label, "outcome": outcome, "answered_signature": answered,
            "aborted_reason": aborted_reason, "attempts": attempts,
            "elapsed_ms": round((time.monotonic() - start) * 1000.0, 2)}


def engine_version(sdk) -> str | None:
    """The server's version as ``major.minor.patch``, or ``None`` if unread.

    ⛔ ``None``, never ``"unknown"`` and never a ``0``: this file, the skip site in
    ``tortoise.search_engine`` and ``tests/test_13108_dense_leg_index_skip.py``
    all name the engine they were measured on, and until ``#13121`` round 14 that
    figure was cited to artifacts which carry no such field. A run writes it here
    so the citation has a home. **An artifact committed before that round has no
    ``engine_version`` key** — its engine is the ``tools/test_lane.py`` pin.
    """
    try:
        version = sdk._get_proj()._get_falkordb_version()
    except Exception:  # noqa: BLE001, RUF100 — an UNMEASURED version is not an error
        return None
    if not version:
        return None
    return ".".join(str(part) for part in version)


def label_census(sdk) -> dict:
    """How many nodes per label, and does the engine hold a VECTOR index for it?

    ⛔ Reported because ``stored`` alone is the 2026-09-24 error repeated: a
    population with no embeddings is not the same fact as an index-less one, and
    only the pair distinguishes them (`tools/dod_5090_probe.py` --census rule).
    """
    proj = sdk._get_proj()
    out: dict = {"labels": {}, "vector_indexes": []}
    for label in ("Object", "Point"):
        rows = proj.g.query(
            f"MATCH (n:{label}) RETURN count(*), count(n.embedding)",
            params={}).result_set
        total, embedded = (rows[0][0], rows[0][1]) if rows else (0, 0)
        out["labels"][label] = {"stored": int(total or 0),
                                "embedded": int(embedded or 0)}
    try:
        idx = proj.g.query("CALL db.indexes()", params={}).result_set
    except Exception as e:  # UNMEASURED, never 0
        out["vector_indexes"] = None
        out["vector_indexes_error"] = f"{type(e).__name__}: {e}"
        return out
    for row in idx:
        # FalkorDB's catalogue row: label, property, types, language, stopwords,
        # entitytype, status, info(…). Read defensively — the shape is the
        # engine's, not ours, and a census that assumes it is the `#5090` class.
        text = " ".join(str(c) for c in row).upper()
        if "VECTOR" in text:
            out["vector_indexes"].append([str(c) for c in row[:4]])
    return out


# ── the measurement ─────────────────────────────────────────────────────────

def _vector_query_once(graph, query_vec, entity_type: str, limit: int,
                       timeout_ms: int,
                       vector_indexed_labels=None) -> dict:
    """ONE shipped dense read, with its trace: mechanism + latency + outcome."""
    from tortoise.search_engine import (
        VECTOR_MECHANISM_KEY,
        run_vector_query,
    )
    trace: list[dict] = []
    start = time.monotonic()
    rows: list = []
    raised = None
    try:
        rows = run_vector_query(graph, query_vec, limit=limit,
                                timeout_ms=timeout_ms, is_embedded=False,
                                entity_type=entity_type, leg_trace=trace,
                                **({"vector_indexed_labels": vector_indexed_labels}
                                   if vector_indexed_labels is not None else {}))
    except Exception as e:  # a raised leg is a SAMPLE, not a crash
        raised = f"{type(e).__name__}: {e}"
    elapsed = (time.monotonic() - start) * 1000.0
    entry = trace[0] if trace else {}
    mech = entry.get(VECTOR_MECHANISM_KEY)
    if mech is not None and mech not in MECHANISMS:
        raise UnknownMechanism(
            f"UNKNOWN mechanism {mech!r} — not in {MECHANISMS} "
            f"(entity_type={entity_type!r})"
        )
    return {
        "entity_type": entity_type,
        "elapsed_ms": round(elapsed, 2),
        "timeout_ms": timeout_ms,
        "mechanism": mech,
        "reason": entry.get("reason"),
        "ran": entry.get("ran"),
        "degraded": entry.get("degraded"),
        "rows": len(rows),
        "raised": raised,
    }


def measure(graph, query_vec, entity_type: str, reads: int, concurrency: int,
            limit: int, timeout_ms: int, vector_indexed_labels=None) -> dict:
    """``reads`` dense reads at ``concurrency`` real threads.

    Concurrency is the honest load lever for THIS bug: the shipped read runs its
    legs on a ``ThreadPoolExecutor`` worker and the store serialises work on the
    graph, so N concurrent reads are exactly the shape a capture fleet presents —
    not a synthetic CPU burn.
    """
    samples: list[dict] = []
    failures: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)
    per_thread = [reads // concurrency + (1 if i < reads % concurrency else 0)
                  for i in range(concurrency)]

    def worker(n: int) -> None:
        barrier.wait()
        local: list[dict] = []
        try:
            for _ in range(n):
                local.append(_vector_query_once(graph, query_vec, entity_type,
                                                limit, timeout_ms,
                                                vector_indexed_labels))
        except BaseException as e:
            # ⛔ A raised sample is recorded on the worker's own thread and turned
            # into a refused arm below. Letting anything escape a thread is how
            # this guard dies silently (see `UnknownMechanism`).
            with lock:
                failures.append(f"{type(e).__name__}: {e}")
        finally:
            with lock:
                samples.extend(local)

    threads = [threading.Thread(target=worker, args=(per_thread[i],),
                                daemon=True)
               for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # ⛔ Integrity BEFORE the summary: an arm that did not complete is never
    # published as a short one.
    refusal = arm_integrity(entity_type, concurrency, reads, len(samples),
                            failures)
    if refusal is not None:
        raise SystemExit(refusal)
    out = summarise(samples)
    out["entity_type"] = entity_type
    out["concurrency"] = concurrency
    out["samples"] = sorted(samples, key=lambda s: s["elapsed_ms"])
    return out


# ── the eval's own window, run concurrently ─────────────────────────────────

def measure_windows(sdk, names: list[str], concurrency: int, limit: int,
                    timeout_ms: int) -> dict:
    """The EVAL'S OWN window (`tools/candidate_recall_11901.py`), run at concurrency.

    One window = the shipped read at ``limit`` + `#2952`'s STRICT predicate
    (:func:`tortoise.search_engine.require_hybrid_read`) over its trace — the
    exact pair the eval's ``window()`` uses, so a mode share measured here is
    the same quantity as the artifact's ``window_modes``.

    ⛔ The eval runs its windows SEQUENTIALLY, so it presents the graph with one
    read at a time. A capture fleet does not: many entities are in flight at
    once and they share one store. That is the difference this arm exists to
    measure — and it is measured, not asserted: the same code, the same corpus,
    the same predicate, only the concurrency differs.
    """
    from tortoise.exceptions import HybridReadUnavailableError
    from tortoise.extractor_v2 import _sanitise_query_text
    from tortoise.search_engine import require_hybrid_read

    modes = collections.Counter()
    reasons = collections.Counter()
    mechanisms = collections.Counter()
    lat: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)
    per_thread = [len(names) // concurrency + (1 if i < len(names) % concurrency
                                                else 0)
                  for i in range(concurrency)]

    def worker(n: int, offset: int) -> None:
        barrier.wait()
        local_modes: collections.Counter = collections.Counter()
        local_reasons: collections.Counter = collections.Counter()
        local_mech: collections.Counter = collections.Counter()
        local_lat: list[float] = []
        for i in range(offset, offset + n):
            trace: list[dict] = []
            t0 = time.monotonic()
            try:
                sdk.tortoise_fts_query(
                    _sanitise_query_text(names[i % len(names)]),
                    entity_type="object", limit=limit, include_terminal=True,
                    _record_reads=False, _valid_time_rerank=False,
                    leg_trace=trace)
            except Exception as e:  # a raised read is a SAMPLED failure
                local_reasons[f"raised:{type(e).__name__}"] += 1
                local_modes["keyword_only"] += 1
                local_lat.append((time.monotonic() - t0) * 1000.0)
                continue
            local_lat.append((time.monotonic() - t0) * 1000.0)
            for entry in trace or []:
                if entry.get("leg") == "vector":
                    local_mech[str(entry.get("mechanism"))] += 1
                    local_reasons[str(entry.get("reason"))] += 1
            try:
                require_hybrid_read(trace)
            except HybridReadUnavailableError:
                local_modes["keyword_only"] += 1
            else:
                local_modes["hybrid"] += 1
        with lock:
            modes.update(local_modes)
            reasons.update(local_reasons)
            mechanisms.update(local_mech)
            lat.extend(local_lat)

    threads, offsets = [], 0
    for n_in in per_thread:
        threads.append(threading.Thread(target=worker, args=(n_in, offsets),
                                        daemon=True))
        offsets += n_in
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = modes.get("hybrid", 0) + modes.get("keyword_only", 0)
    return {
        "concurrency": concurrency,
        "windows": total,
        "window_modes": dict(sorted(modes.items())),
        "keyword_only_share": (round(modes.get("keyword_only", 0) / total, 4)
                               if total else None),
        "vector_reason": dict(sorted(reasons.items())),
        "vector_mechanism": dict(sorted(mechanisms.items())),
        "latency_ms": {
            "p50": percentile(lat, 50),
            "p95": percentile(lat, 95),
            "p99": percentile(lat, 99),
            "max": max(lat) if lat else None,
        },
    }


# ── the box's own state (a load figure with no context is not a measurement) ─

def box_load() -> dict:
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:  # pragma: no cover - non-POSIX
        one = five = fifteen = None
    return {
        "loadavg": [round(one, 2), round(five, 2), round(fifteen, 2)]
        if one is not None else None,
        "cpus": os.cpu_count(),
    }


def spawn_external_load(n: int) -> list[subprocess.Popen]:
    """``n`` real embedder processes — the capture write path's own work.

    Real work, not a burn loop: each child encodes a batch of strings through the
    SAME ``EmbeddingModel`` the product uses, so the contention it creates is the
    contention a capture fleet creates (torch intra-op threads included).
    """
    code = (
        "import sys\n"
        "from tortoise.embeddings import EmbeddingModel\n"
        "m = EmbeddingModel.get()\n"
        "if m is None:\n"
        "    sys.exit('no embedder')\n"
        "batch = [f'entity name number {i} in a captured conversation'\n"
        "         for i in range(256)]\n"
        "while True:\n"
        "    m.encode(batch)\n"
    )
    return [subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            for _ in range(n)]


def stop_external_load(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()


# ── main ────────────────────────────────────────────────────────────────────

def population_measured(census: dict, labels: list[str]) -> dict:
    """The population actually IN THE GRAPH, per census label.

    ⛔ The two vocabularies differ and mixing them produces a field that reads as
    measured and is empty: the census (:func:`label_census`) is keyed by LABEL
    (``"Object"``, ``"Point"``), while ``--labels`` names ENTITY TYPES
    (``object``, ``point``). Looking the entity type up directly answered
    ``None`` for every arm — which is why `#13121` round 4's fix was a no-op and
    round 5 caught it. :data:`LABEL_BY_ENTITY` is the ONE mapping, and it is the
    same one :func:`probe_index_attempt` is called through.

    ``None`` per label means the census could not be read for it (UNMEASURED),
    never 0 — the distinction this whole file is built on.
    """
    out: dict = {}
    for entity_type in labels:
        label = LABEL_BY_ENTITY.get(entity_type, entity_type)
        entry = (census.get("labels") or {}).get(label)
        out[label] = (entry or {}).get("stored") if entry is not None else None
    return out


def seeded_names(seeded: dict | None) -> list[str]:
    """The population a `seed()` report carries, as a plain list of names.

    ⛔ `seed()` returns `"names": [r["name"] for r in pop]` — a list of STRINGS.
    A `[r["name"] for r in seeded["names"]]` here therefore indexes a `str` with
    a `str` key and raises `TypeError: string indices must be integers` — which
    killed the natural fresh-graph `--windows` run (the mode that produced this
    PR's headline `window_modes`) before a single window was measured, with an
    error naming neither the flag nor the mode (`#13121` round 6 F1). Pure, so
    the shape is pinned without a graph.
    """
    if not seeded:
        return []
    return list(seeded.get("names") or [])


def worktree_state() -> dict:
    """The tree the figure was measured ON, not just the commit it names.

    ⛔ `code_head` alone is not provenance: a run against an UNCOMMITTED draft
    records the last commit, and the artifact then says the measurement predates
    the very fix it measures (`#13121` review round 4, F3 —
    `fix-8000-paired.json` names `d93c4c99e9`, a commit with no
    `vector_indexed_labels`, while its `hint` arm demonstrably ran one). Recording
    the dirty flag and a digest of the tracked diff makes that question
    answerable from the artifact instead of from a reviewer's forensics.

    Never raises: a figure is worth more than its provenance label, and an
    unreadable `git` is reported as UNMEASURED, never as clean.
    """
    def _git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                              capture_output=True, text=True,
                              check=False).stdout

    status = _git("status", "--porcelain")
    diff = _git("diff", "HEAD")
    if not status and not diff:
        # An EMPTY status is only meaningful if `git` actually answered; a
        # failed invocation also returns ''. Distinguish them by asking for the
        # head again, which we already have, so use the return codes.
        probe = subprocess.run(["git", "-C", str(REPO_ROOT), "status",
                                "--porcelain"], capture_output=True,
                               text=True, check=False)
        if probe.returncode != 0:
            return {"dirty": None, "reason": f"git status exited "
                    f"{probe.returncode}: {probe.stderr.strip()[:200]}"}
    dirty = bool(status.strip() or diff.strip())
    return {
        "dirty": dirty,
        "changed_paths": sorted({line[3:].strip() for line in
                                 status.splitlines() if line.strip()}),
        "diff_sha256": (hashlib.sha256(diff.encode()).hexdigest()[:16]
                        if dirty else None),
    }


def run(args: argparse.Namespace) -> int:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    if not uri.startswith(("docker://", "redis://")):
        print("REFUSED: no live FalkorDB in TORTOISE_DB_URI — a latency figure "
              "measured on the embedded backend is not a figure.\n"
              "  uri=\"$(uv run python tools/test_lane.py uri)\" && eval \"$uri\"",
              file=sys.stderr)
        return 3

    from tortoise.embeddings import EmbeddingModel
    from tortoise.sdk import TortoiseSDK

    model = EmbeddingModel.get()
    if model is None:
        print("REFUSED: no embedder — the object arm's population would carry no "
              "vectors and the probe would measure an empty scan "
              f"({EmbeddingModel.absent_reason()}).\n"
              "  uv sync --extra embeddings --extra test --extra parity",
              file=sys.stderr)
        return 3

    sdk = TortoiseSDK(namespace=args.namespace,
                      graph_name=args.graph or f"dense13108_{int(time.time())}")

    seeded = None
    if not args.no_seed:
        t0 = time.time()
        seeded = seed(sdk, args.population, args.sessions_dir)
        seeded["elapsed_s"] = round(time.time() - t0, 1)

    census = label_census(sdk)
    graph = sdk._get_proj().g

    # The query vector is a population member's own vector: the encoder is out of
    # the loop on purpose (see the module docstring — it runs OUTSIDE the cap in
    # the shipped read, so including it here would measure the wrong thing).
    rows = graph.query(
        "MATCH (o:Object) WHERE o.id STARTS WITH 'obj-13108' AND o.embedding IS NOT NULL "
        "RETURN o.embedding LIMIT 1", params={}).result_set
    if not rows:
        print("REFUSED: no embedded :Object in the graph — nothing to scan",
              file=sys.stderr)
        return 3
    query_vec = list(rows[0][0])

    index_attempts = [probe_index_attempt(
        graph, query_vec, LABEL_BY_ENTITY.get(t, "Point"), args.limit,
        args.timeout_ms) for t in args.labels]

    # The projection's OWN measured inventory (`_record_vector_index_inventory`).
    # ⛔ The BEHAVIOURAL half (`_vector_indexed_labels_for_reads`), because that
    # is the one the shipped read actually consults (`tortoise/sdk.py` forwards
    # it at all three call sites) — reading the REPORTING pair here made the
    # `--compare-hint` arms two copies of the same path (`#13121` round 5 F2).
    # It is also the half whose `None` lane is genuinely "not measured", which is
    # exactly what `nohint` needs to mean.
    indexed = getattr(sdk._get_proj(), "_vector_indexed_labels_for_reads", None)

    arms: dict = {}
    for entity_type in args.labels:
        for conc in args.concurrency:
            procs: list[subprocess.Popen] = []
            try:
                if args.external_load:
                    procs = spawn_external_load(args.external_load)
                    time.sleep(args.load_warmup_s)
                load = box_load()
                label = LABEL_BY_ENTITY.get(entity_type, "Point")
                hints = {"hint": indexed}
                if args.compare_hint:
                    hints["nohint"] = None
                for hint_name, hint in hints.items():
                    arm = measure(graph, query_vec, entity_type, args.reads,
                                  conc, args.limit, args.timeout_ms, hint)
                    arm["load"] = load
                    arm["external_load_procs"] = len(procs)
                    arm["index_hint"] = (sorted(hint) if hint is not None
                                         else None)
                    arm["label"] = label
                    key = f"{entity_type}[{hint_name}]@c{conc}"
                    arms[key] = arm
                    print(f"\n== {entity_type} [{hint_name}] @ concurrency "
                          f"{conc} (load {load['loadavg']}/{load['cpus']} cpus, "
                          f"{len(procs)} external embedder procs) ==")
                    print(f"  mechanism {arm['mechanism']}  "
                          f"reason {arm['reason']}")
                    print(f"  latency_ms p50 {arm['latency_ms']['p50']} "
                          f"p95 {arm['latency_ms']['p95']} "
                          f"p99 {arm['latency_ms']['p99']} "
                          f"max {arm['latency_ms']['max']}")
                    print(f"  over {args.timeout_ms}ms: {arm['over_budget']}"
                          f"/{arm['n']}  degraded: {arm['degraded']}"
                          f"  zero-rows: {arm['count']['zero_rows']}")
            finally:
                stop_external_load(procs)

    # ── the eval's own window, at concurrency (the issue's `window_modes`) ──
    windows: dict = {}
    if args.windows:
        names = seeded_names(seeded)
        if not names:
            names = [r[0] for r in graph.query(
                "MATCH (o:Object) WHERE o.id STARTS WITH 'obj-13108' "
                "RETURN o.name ORDER BY o.id", params={}).result_set]
        names = names[:args.windows]
        for conc in args.concurrency:
            # ⛔ THE LOAD MUST BE ALIVE DURING THE WINDOWS TOO. Measured the hard
            # way: an earlier version stopped the external load before this loop,
            # so the `window_modes` split was taken on an idle box while the arms
            # above it were taken under load — two different machines in one table.
            procs: list[subprocess.Popen] = []
            try:
                if args.external_load:
                    procs = spawn_external_load(args.external_load)
                    time.sleep(args.load_warmup_s)
                load = box_load()
                # `hint` = the shipped read (projection's measured inventory);
                # `nohint` = the SAME read with that inventory forced to None,
                # which IS the pre-#13108 behaviour (None = "not measured →
                # attempt as before"). One process, one box, one load.
                # ⚠️ The override must write `_vector_indexed_labels_for_reads`
                # — the behavioural half the shipped read consults. Writing the
                # REPORTING pair left both arms identical (`#13121` round 5 F2).
                variants = {"hint": indexed}
                if args.windows_compare_hint:
                    variants["nohint"] = None
                for hint_name, hint in variants.items():
                    proj = sdk._get_proj()
                    restore = getattr(proj, "_vector_indexed_labels_for_reads",
                                      None)
                    proj._vector_indexed_labels_for_reads = hint
                    try:
                        arm = measure_windows(sdk, names, conc, args.limit,
                                              args.timeout_ms)
                    finally:
                        proj._vector_indexed_labels_for_reads = restore
                    arm["load"] = load
                    arm["external_load_procs"] = len(procs)
                    arm["index_hint"] = (sorted(hint) if hint is not None
                                         else None)
                    windows[f"windows[{hint_name}]@c{conc}"] = arm
                    print(f"\n== eval windows [{hint_name}] @ concurrency "
                          f"{conc} (load {load['loadavg']}/{load['cpus']} cpus, "
                          f"{len(procs)} external procs) ==")
                    print(f"  {arm['window_modes']}  keyword-only share "
                          f"{arm['keyword_only_share']}")
                    print(f"  vector reason {arm['vector_reason']}")
                    print(f"  latency_ms p50 {arm['latency_ms']['p50']} "
                          f"p95 {arm['latency_ms']['p95']} "
                          f"max {arm['latency_ms']['max']}")
            finally:
                stop_external_load(procs)

    report = {
        "issue": 13108,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code_head": subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False).stdout.strip(),
        # #13121 review round 4 (F3): a head is not the tree it was measured on.
        "worktree": worktree_state(),
        "uri_graph": args.graph or sdk._graph_name,
        # #13121 round 14 (F, version citation): the prose names an engine; the
        # artifact must be able to answer for it. `None` when unread, never a
        # fabricated string.
        "engine_version": engine_version(sdk),
        "config": {
            # #13121 review round 4 (F4), corrected in round 5 (F1): the
            # `--population` ARGUMENT, which is NOT the population measured on a
            # `--no-seed` run over an already-seeded graph. Four artifacts said
            # `population: 1000` beside their own `census` of 8,000 for exactly
            # this reason; the reading below is the one to quote.
            # ⛔ The census is keyed by LABEL (`"Object"`, `"Point"`), the CLI by
            # ENTITY TYPE (`object`, `point`). Looking the entity type up directly
            # answered `None` for every arm — a field that reads as measured and
            # is empty, which is the class this file refuses. `LABEL_BY_ENTITY`
            # is the ONE mapping (it is what `probe_index_attempt` already uses).
            "population_arg": args.population,
            "population_measured": population_measured(census, args.labels),
            "reads": args.reads,
            "concurrency": list(args.concurrency),
            "limit": args.limit,
            "timeout_ms": args.timeout_ms,
            "labels": list(args.labels),
            "external_load": args.external_load,
            "sessions_dir": args.sessions_dir,
        },
        "box": box_load(),
        "census": census,
        "index_attempt": index_attempts,
        "windows": windows,
        "seed": seeded,
        "arms": arms,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True))
        print(f"\nwrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--population", type=int, default=1000,
                   help="names written to BOTH labels (the eval's own 1,000)")
    p.add_argument("--reads", type=int, default=100,
                   help="dense reads per arm")
    p.add_argument("--concurrency", default="1",
                   help="comma-separated thread counts; 1 is the at-rest arm")
    p.add_argument("--limit", type=int, default=3,
                   help="the capture prior's own window")
    p.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    p.add_argument("--compare-hint", action="store_true",
                   help="also measure each arm WITHOUT the measured index "
                        "inventory — the paired before/after of #13108's fix")
    p.add_argument("--labels", default="object,point",
                   help="entity types to measure (object=unindexed, point=indexed)")
    p.add_argument("--external-load", type=int, default=0,
                   help="real embedder processes to run during the arm")
    p.add_argument("--windows-compare-hint", action="store_true",
                   help="run the window arm twice — with the measured inventory "
                        "and with it forced to None (pre-#13108 behaviour) — "
                        "under ONE load, so the pair is matched")
    p.add_argument("--windows", type=int, default=0,
                   help="also run N of the EVAL'S OWN windows at each --concurrency "
                        "(the `window_modes` split, on the real read path)")
    p.add_argument("--load-warmup-s", type=float, default=20.0)
    p.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    p.add_argument("--namespace", default="dense13108")
    p.add_argument("--graph", default=None)
    p.add_argument("--no-seed", action="store_true",
                   help="measure an already-seeded graph (the ids must still match)")
    p.add_argument("--out", default=None)
    ns = p.parse_args(argv)
    ns.concurrency = [int(c) for c in str(ns.concurrency).split(",") if c]
    ns.labels = [s for s in str(ns.labels).split(",") if s]
    return run(ns)


if __name__ == "__main__":
    raise SystemExit(main())
