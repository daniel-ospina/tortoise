#!/usr/bin/env python3
"""The two-arm S3 prior-injection ablation (#10463 / epic #5087).

THE QUESTION. The extractor epic's central hypothesis is that *telling extraction what already
exists in the graph* (S3 prior-injection) *reduces what it creates*. This probe measures it on the
frozen ``tests/eval/write_path`` corpus as a two-arm ablation over ``TortoiseSDK.capture_session``
(the real entry point), one full replay of the 7 sessions per arm, onto a FRESH disposable graph
per arm:

* **OFF arm** — ``extractor_v2.search_graph`` is REPLACED in-process by a stub returning a healthy
  zero-result search (``degraded=False``, no entities/points/events, ``queries_run=0``, a reason
  naming ``OFF-ARM``). The writer is told nothing exists. A degraded lookup would ALSO tell the
  writer nothing exists, but through a failure — the stub keeps the arm a clean counterfactual.
* **ON arm** — the REAL ``search_graph``, wrapped only by a call counter so the log can say the
  function actually executed.

ARM INTEGRITY IS ASSERTED FROM THE RUN RECEIPT, never from a flag: each session's ``search`` block
must show ``mode="real"``, ``degraded=False`` and ``queries_run>=1`` in ON, and ``queries_run==0``
with the ``OFF-ARM`` reason in OFF. A run whose ON lookup degraded is a measurement of the
degradation (#3679), so it is reported ``ARM-INVALID`` and no ratio is printed.

⚠️ ``queries_failed`` is NOT a receipt field at this head. ``search_graph`` degrades the WHOLE
search on the first query failure (``tortoise/extractor_v2.py::search_graph``), so
``queries_failed == 0`` is asserted as ``degraded == False``; the log says so beside the number
rather than implying the receipt carries a key it does not.

THE BARS ARE NOT TYPED HERE. They are parsed out of the pre-registration COMMIT
(``git show <sha>:<prereg>``), so a threshold edited after the numbers are in cannot be served:
``--prereg`` names the commit, and the record's ``source_sha`` is asserted to be an ancestor of
``HEAD`` with no non-evidence file differing between them.

The node census is ``docs/evidence/7949-capstone/probes/capstone_measure.py::census``, imported
READ-ONLY and reused; this module does not implement a second census (#10463 forbids one).

Exit codes: 0 = both arms measured and a verdict was written; 3 = REFUSED / ARM-INVALID (no
verdict, never a 0); 2 = the invocation itself is wrong (e.g. an ``--out`` escaping the evidence
bundle).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The corpus package is imported from the repo root, so the root must be on sys.path when this
# file is run as a script (`tools/` would otherwise be the only entry) — the same reason
# capstone_measure.py inserts it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVIDENCE_DIR = ROOT / "docs/evidence/5087-hypothesis"
PREREG_REL = "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md"
CAPSTONE_CENSUS = ROOT / "docs/evidence/7949-capstone/probes/capstone_measure.py"
PINS_FENCE = "```yaml prereregistration-pins"
ARMS = ("OFF", "ON")
VERDICT_SUPPORTED = "HYPOTHESIS-SUPPORTED"
VERDICT_FALSIFIED = "HYPOTHESIS-FALSIFIED"
VERDICT_REFUSED = "REFUSED"
VERDICT_ARM_INVALID = "ARM-INVALID"


# ── the pre-registration (read out of the COMMIT, never the worktree) ──────

def show(sha: str, rel: str) -> str:
    """``<sha>:<rel>`` as text — the frozen artifact, not the working copy."""
    proc = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"s3-ablation: REFUSED — {rel} is not readable at {sha}: "
                         f"{proc.stderr.strip()[:300]}")
    return proc.stdout


def parse_pins(text: str) -> dict:
    """The ``yaml prereregistration-pins`` fenced block."""
    import yaml
    start = text.find(PINS_FENCE)
    if start < 0:
        raise SystemExit(f"s3-ablation: REFUSED — the pre-registration carries no "
                         f"`{PINS_FENCE}` block; the bars cannot be read")
    body_start = text.find("\n", start)
    end = text.find("\n```", body_start)
    if body_start < 0 or end < 0:
        raise SystemExit("s3-ablation: REFUSED — the pins fence is unterminated")
    pins = yaml.safe_load(text[body_start:end])
    if not isinstance(pins, dict):
        raise SystemExit("s3-ablation: REFUSED — the pins block is not a mapping")
    for key in ("hypothesis_bars", "corpus", "source_sha", "denominator_floors"):
        if key not in pins:
            raise SystemExit(f"s3-ablation: REFUSED — the pins block carries no {key!r}; "
                             "a bar this probe had to guess is not a pre-registered bar")
    return pins


def changed_files(source_sha: str, head: str) -> list[str]:
    """Files differing between the pinned source and the head being measured."""
    proc = subprocess.run(["git", "diff", "--name-only", source_sha, head],
                          cwd=str(ROOT), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"s3-ablation: REFUSED — cannot diff {source_sha}..{head}: "
                         f"{proc.stderr.strip()[:300]}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def is_evidence_or_tool(path: str) -> bool:
    """True when ``path`` is bundle/tooling, i.e. not production code under test."""
    return path.startswith("docs/evidence/") or path.startswith("tools/")


def assert_source_sha(source_sha: str, head: str) -> list[str]:
    """The pinned sha must really be the tree that runs.

    Ancestor-checked, and every differing file must be evidence or tooling — so the numbers
    describe the pinned code, not some later production edit that slipped in beside the run.
    """
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", source_sha, head],
                          cwd=str(ROOT), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"s3-ablation: REFUSED — source_sha {source_sha} is not an "
                         f"ancestor of HEAD {head}; the pinned tree is not the tree that runs")
    changed = changed_files(source_sha, head)
    production = [path for path in changed if not is_evidence_or_tool(path)]
    if production:
        raise SystemExit(
            f"s3-ablation: REFUSED — {len(production)} production file(s) differ between the "
            f"pinned source_sha {source_sha} and HEAD {head}: {production[:5]} — the pinned sha "
            "must be the tree the run executes"
        )
    return changed


# ── the corpus ─────────────────────────────────────────────────────────────

def corpus_state(pins: dict) -> tuple[list[str], str]:
    """(session ids, fixtures_hash) re-derived from the tree, refused if it moved."""
    from tests.eval.write_path.corpus import compute_fixtures_hash, session_ids

    sessions = session_ids()
    actual = compute_fixtures_hash()
    want = (pins.get("corpus") or {}).get("fixtures_hash")
    if want != actual:
        raise SystemExit(f"s3-ablation: REFUSED — the corpus in this checkout hashes to "
                         f"{actual}, the pre-registration froze {want} — a different "
                         "corpus is refused, never scored")
    frozen_n = (pins.get("corpus") or {}).get("sessions")
    if isinstance(frozen_n, int) and len(sessions) != frozen_n:
        raise SystemExit(f"s3-ablation: REFUSED — the pre-registration freezes {frozen_n} "
                         f"sessions, this corpus has {len(sessions)}")
    return sessions, actual


# ── the census (reused, never reimplemented) ───────────────────────────────

def load_census():
    """``capstone_measure.census`` — the ONE census (#10463 forbids a second)."""
    spec = importlib.util.spec_from_file_location("_capstone_census", CAPSTONE_CENSUS)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:  # pragma: no cover - import machinery guard
        raise SystemExit(f"s3-ablation: REFUSED — cannot load {CAPSTONE_CENSUS}")
    spec.loader.exec_module(module)
    census = getattr(module, "census", None)
    if not callable(census):  # pragma: no cover - the artifact moved
        raise SystemExit("s3-ablation: REFUSED — capstone_measure.census is not callable")
    return census


def created_counts(data: dict) -> dict:
    """The create metrics, read off the REUSED census.

    ``extraction_nodes`` is the primary denominator/numerator: extraction-claim Points plus the
    ``Object`` and ``Event`` nodes the run minted. Turn-row Points (``is_episodic``) and operator
    Points are EXCLUDED on purpose — both arms write them under the same rules, and counting them
    would push the ratio toward 1.0 for reasons that have nothing to do with prior-injection. The
    wider ``Σ label_counts`` delta is carried beside it as a disclosed aside.
    """
    composition = data.get("point_composition") or {}
    per_label = data.get("per_label") or {}
    claims = int(composition.get("extraction_claims") or 0)
    objects = int((per_label.get("Object") or {}).get("nodes") or 0)
    events = int((per_label.get("Event") or {}).get("nodes") or 0)
    return {
        "extraction_claims": claims,
        "objects": objects,
        "events": events,
        "extraction_nodes": claims + objects + events,
        "label_instances": sum(int(count) for count in (data.get("label_counts") or {}).values()),
        "points": int(data.get("points") or 0),
        "operator_edges": sum(int(count) for count in
                              (data.get("operator_relationships") or {}).values()),
    }


def read_point_contents(uri: str) -> list[dict]:
    """Every ``:Point`` in the arm's graph, as grading input (NOT a census).

    ``tests.eval.write_path.grading`` is the corpus's own matcher and it takes points, so the
    real-fact guard needs their contents. This is a grading read of one property; the node
    accounting above comes from the reused census.
    """
    from tortoise.projection import FalkorProjection

    projection = FalkorProjection.from_uri(uri)
    return [
        {"point_id": str(row[0]), "content": str(row[1] or "")}
        for row in projection.g.query(
            "MATCH (p:Point) RETURN p.id AS id, p.content AS content").result_set
        if row and row[0]
    ]


def real_facts(arm_gold: dict[str, dict], points: list[dict]) -> dict:
    """Planted units recovered by content, over the ARM's whole graph.

    Scored over the arm's cumulative Point set, not over the points written in the session: the
    hypothesis predicts FOLDING onto existing canonical Points, which writes no new Point, so a
    written-in-session scope would charge the ON arm for the exact behaviour under test. A unit is
    recovered when any Point in the arm's graph retains its anchor.
    """
    from tests.eval.write_path.grading import macro_survival_counts

    survived = 0
    total = 0
    per_session: dict[str, dict] = {}
    for sid, gold in arm_gold.items():
        counts = macro_survival_counts(gold, points)
        survived += int(counts["survived"])
        total += int(counts["total"])
        per_session[sid] = {"survived": int(counts["survived"]),
                            "total": int(counts["total"])}
    return {"survived": survived, "total": total, "per_session": per_session}


# ── the arms ───────────────────────────────────────────────────────────────

def graph_uri(base_uri: str, graph: str) -> str:
    """Address ``graph`` on the container ``base_uri`` points at (last segment replaced)."""
    stem, sep, query = base_uri.partition("?")
    base = stem.rsplit("/", 1)[0]
    return f"{base}/{graph}" + (f"?{query}" if sep else "")


def off_arm_stub(mode: str) -> dict:
    """What the OFF arm's S3 returns: a HEALTHY search that found nothing."""
    return {
        "mode": mode,
        "degraded": False,
        "reason": ("OFF-ARM: S3 prior-injection suppressed by the probe "
                   "(search_graph replaced; zero priors injected)"),
        "entities": [], "points": [], "events": [], "queries_run": 0,
    }


def arm_s3_state(arm: str, search: dict, witness_source: str = "receipt") -> dict:
    """The receipt's S3 state, and whether it PROVES the arm it claims to be."""
    search = search or {}
    queries_run = int(search.get("queries_run") or 0)
    reason = str(search.get("reason") or "")
    degraded = bool(search.get("degraded"))
    mode = search.get("mode")
    if arm == "ON":
        resolved = (not degraded) and queries_run >= 1 and mode == "real"
        note = "resolved" if resolved else f"NOT resolved (degraded={degraded}, queries_run={queries_run}, mode={mode!r})"
    else:
        resolved = False
        proven_off = (not degraded) and queries_run == 0 and reason.startswith("OFF-ARM")
        note = "suppressed (OFF-ARM)" if proven_off else "NOT proven suppressed"
        return {"queries_run": queries_run, "degraded": degraded, "mode": mode,
                "reason": reason, "state": note, "proven": proven_off}
    return {"queries_run": queries_run, "degraded": degraded, "mode": mode,
            "reason": reason, "state": note, "proven": resolved}


def run_arm(arm: str, graph: str, sessions: list[str], census, golds: dict[str, dict],
            log) -> dict:
    """One full replay of the corpus into a fresh graph, with the arm's S3 behaviour."""
    import tortoise.extractor_v2 as ev
    from tortoise.sdk import TortoiseSDK

    real_search = ev.search_graph
    calls = {"real": 0}
    witness: dict = {}

    def counting(*args, **kwargs):
        calls["real"] += 1
        result = real_search(*args, **kwargs)
        witness.clear()
        witness.update(result or {})
        return result

    def off_stub(*args, **kwargs):
        result = off_arm_stub(ev.resolve_backend_mode())
        witness.clear()
        witness.update(result)
        return result

    ev.search_graph = counting if arm == "ON" else off_stub
    base_uri = os.environ["TORTOISE_DB_URI"]
    uri = graph_uri(base_uri, graph)
    rows: list[dict] = []
    try:
        sdk = TortoiseSDK(graph_name=graph)
        before = created_counts(census(uri))
        for sid in sessions:
            started = time.time()
            receipt = sdk.capture_session(_turns(sid), session_id=sid)
            stats = receipt.get("stats") or {}
            # ⚠️ THE RECEIPT CARRIES NO `search` BLOCK ON THIS LANE. `capture_session` ->
            # `_extract_session_v2` never surfaces S3's report onto the meta it returns (only
            # `_commit_session_v2` does), so the arm's S3 state is witnessed from the value the
            # S3 function ACTUALLY RETURNED during this session — recorded by the wrapper above,
            # which is the real function's own output, not a flag. The receipt is used when it
            # does carry the block, so this probe cannot be accused of preferring its own copy.
            receipt_search = receipt.get("search") or {}
            witness_source = "receipt" if receipt_search else "search_graph-return"
            s3_blob = receipt_search or dict(witness)
            after = created_counts(census(uri))
            created = {key: after[key] - before[key] for key in after}
            rows.append({
                "session_id": sid,
                "ok": bool(receipt.get("ok")),
                "created": created,
                "llm_calls": int(((stats.get("llm") or {}).get("calls") or 0)),
                "s3": {**arm_s3_state(arm, s3_blob), "witness_source": witness_source},
                "errors": list(receipt.get("errors") or [])[:3],
                "elapsed_s": round(time.time() - started, 1),
            })
            before = after
            log(f"  [{arm}] {sid}: created={created['extraction_nodes']} "
                f"(claims={created['extraction_claims']} objects={created['objects']} "
                f"events={created['events']}) s3={rows[-1]['s3']['state']} "
                f"llm_calls={rows[-1]['llm_calls']} ok={rows[-1]['ok']} "
                f"({rows[-1]['elapsed_s']}s)")
    finally:
        ev.search_graph = real_search
    final = created_counts(census(uri))
    return {
        "arm": arm,
        "graph": graph,
        "uri": uri,
        "sessions": rows,
        "real_search_calls": calls["real"],
        "real_facts": real_facts(golds, read_point_contents(uri)),
        "totals": {key: sum(row["created"][key] for row in rows) for key in final},
        "final": final,
        "llm_calls": sum(row["llm_calls"] for row in rows),
    }


def _turns(sid: str) -> list[dict]:
    from tests.eval.write_path.corpus import load_fixture

    turns = (load_fixture(sid) or {}).get("conversation")
    if not isinstance(turns, list) or not turns:
        raise SystemExit(f"s3-ablation: REFUSED — fixture {sid} carries no conversation")
    return turns


# ── the arithmetic the pre-registration fixes ──────────────────────────────

def concentration(pairs: list[tuple[int, int]]) -> dict:
    """The largest single session's share of the total create REDUCTION.

    ``pairs`` are ``(off_created, on_created)`` per session. Reductions are floored at 0 so a
    session that INCREASED cannot subsidise the shares. ``share`` is ``None`` when nothing fell at
    all — an undefined statistic, never a 0 that reads like a clean result.
    """
    reductions = [max(0, off - on) for off, on in pairs]
    total = sum(reductions)
    if total <= 0:
        return {"share": None, "largest_session_reduction": 0, "total_reduction": 0}
    return {"share": max(reductions) / total,
            "largest_session_reduction": max(reductions),
            "total_reduction": total}


def decide(bars: dict, floors: dict, off: dict, on: dict) -> dict:
    """The pre-registered verdict mapping, applied to the two arms.

    Order is fixed by the pre-registration: arm integrity first (no ratio from an arm whose S3 did
    not resolve), then the denominator floors, then the primary bar, then the guards.
    """
    integrity = [row["s3"]["proven"] for row in off["sessions"] + on["sessions"]]
    proven = sum(1 for flag in integrity if flag)
    required = int(bars["arm_integrity_required"])
    if proven < required:
        broken = [f"{row['session_id']}:{'ON' if row in on['sessions'] else 'OFF'}"
                  for row in off["sessions"] + on["sessions"] if not row["s3"]["proven"]]
        return {"verdict": VERDICT_ARM_INVALID,
                "why": f"E3 {proven} of {required} session-arms prove their S3 state; "
                       f"unproven: {broken[:6]}",
                "ratio": None, "qualifier": None}

    created_off = off["totals"]["extraction_nodes"]
    created_on = on["totals"]["extraction_nodes"]
    if created_off < int(floors["created_nodes_off"]):
        return {"verdict": VERDICT_REFUSED,
                "why": f"the OFF arm created {created_off} extraction nodes, below the "
                       f"pre-registered floor of {floors['created_nodes_off']} — the corpus "
                       "cannot carry the bar", "ratio": None, "qualifier": None}
    if int(off["real_facts"]["survived"]) < int(floors["real_facts_off"]):
        return {"verdict": VERDICT_REFUSED,
                "why": f"the OFF arm recovered {off['real_facts']['survived']} planted units, "
                       f"below the pre-registered floor of {floors['real_facts_off']}",
                "ratio": None, "qualifier": None}

    ratio = created_on / created_off if created_off else None
    pairs = [(sum(row["created"]["extraction_nodes"] for row in off["sessions"] if row["session_id"] == sid),
              sum(row["created"]["extraction_nodes"] for row in on["sessions"] if row["session_id"] == sid))
             for sid in [row["session_id"] for row in off["sessions"]]]
    conc = concentration(pairs)
    qualifier = None
    if conc["share"] is not None and conc["share"] > float(bars["concentration_max"]):
        qualifier = "SINGLE-SESSION-DRIVEN"
    guard_facts = (on["real_facts"]["survived"] >= off["real_facts"]["survived"])
    guard_operators = (on["totals"]["operator_edges"] >= off["totals"]["operator_edges"])
    primary_met = ratio is not None and ratio <= float(bars["primary_ratio_max"])
    fell = sum(1 for off_n, on_n in pairs if on_n < off_n)
    if primary_met and guard_facts and guard_operators:
        verdict = VERDICT_SUPPORTED
    elif primary_met:
        broken = "real_facts" if not guard_facts else "operator_edges"
        verdict = VERDICT_FALSIFIED
    else:
        broken = None
        verdict = VERDICT_FALSIFIED
    payload = {"verdict": verdict, "ratio": ratio, "qualifier": qualifier,
               "primary_met": primary_met, "guard_facts": guard_facts,
               "guard_operators": guard_operators, "concentration": conc,
               "sessions_fell": fell, "created_off": created_off, "created_on": created_on,
               "cost_ratio": (on["llm_calls"] / off["llm_calls"]) if off["llm_calls"] else None}
    if primary_met and not (guard_facts and guard_operators):
        payload["why"] = (f"E1 met (ratio {ratio:.3f} <= {bars['primary_ratio_max']}) but "
                          f"GUARD-VIOLATED: {broken} fell — volume bought by extracting less "
                          "real content is not a confirmation")
        payload["verdict_text"] = f"HYPOTHESIS-FALSIFIED (ratio {ratio:.3f}, GUARD-VIOLATED: {broken})"
    elif primary_met:
        payload["why"] = (f"E1 met and both guards held (facts {on['real_facts']['survived']} "
                          f">= {off['real_facts']['survived']}, operator edges "
                          f"{on['totals']['operator_edges']} >= {off['totals']['operator_edges']})")
    else:
        payload["why"] = (f"E1 not met: ratio {ratio:.3f} > {bars['primary_ratio_max']} — telling "
                          "extraction what exists does not reduce what it creates")
    return payload


# ── entry point ────────────────────────────────────────────────────────────

def head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                          capture_output=True, text=True, check=True).stdout.strip()


def inside_evidence(path: Path) -> bool:
    try:
        path.resolve().relative_to(EVIDENCE_DIR.resolve())
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Two-arm S3 prior-injection ablation (#10463).")
    ap.add_argument("--prereg", required=True,
                    help="the commit sha the pre-registration is read from")
    ap.add_argument("--out", required=True,
                    help="log path INSIDE docs/evidence/5087-hypothesis/ (the V4 rule refuses an "
                         "escaping evidence path)")
    ap.add_argument("--arm-order", default="OFF,ON",
                    help="corpus replay order of the two arms (default: OFF,ON)")
    ap.add_argument("--graph-prefix", default=None)
    ap.add_argument("--json", action="store_true", help="also print the record as JSON")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    if not inside_evidence(out_path):
        print(f"s3-ablation: REFUSED — --out {out_path} escapes the evidence bundle "
              f"{EVIDENCE_DIR} (the gate's V4 rule refuses an escaping evidence path)")
        return 2

    head = head_sha()
    prereg_text = show(args.prereg, PREREG_REL)
    pins = parse_pins(prereg_text)
    bars = pins["hypothesis_bars"]
    floors = pins["denominator_floors"]
    changed = assert_source_sha(str(pins["source_sha"]), head)
    sessions, corpus_hash = corpus_state(pins)

    lines: list[str] = []

    def log(message: str) -> None:
        lines.append(message)
        print(message, flush=True)

    log("=== s3-ablation (#10463) — does telling extraction what exists reduce what it creates? ===\n")
    log(f"prereg={args.prereg} (bars read from the COMMIT, not the worktree)")
    log(f"source_sha={pins['source_sha']} head={head}")
    log(f"source_sha..head changed files ({len(changed)}, all evidence/tooling): {changed or '(none)'}")
    log(f"corpus=tests/eval/write_path sessions={len(sessions)} fixtures_hash={corpus_hash}")
    log(f"bars: primary_ratio_max={bars['primary_ratio_max']} "
        f"guard_ratio_min={bars['guard_ratio_min']} "
        f"arm_integrity_required={bars['arm_integrity_required']} "
        f"concentration_max={bars['concentration_max']} cost_ratio_max={bars['cost_ratio_max']}")
    log(f"floors: {floors}")
    log("queries_failed: the receipt carries no such field at this head; search_graph degrades "
        "the WHOLE search on the first query failure, so queries_failed==0 is asserted as "
        "degraded==False (stated here, not implied)")
    log("S3 WITNESS: the `capture_session` lane's receipt carries NO `search` block (only "
        "`_commit_session_v2` surfaces one), so each session's S3 state is asserted from the "
        "value the S3 function ACTUALLY RETURNED during that session — recorded by the probe's "
        "wrapper around the real function (ON) / stub (OFF), never from a flag. This is a "
        "disclosed deviation from the pre-registration's wording 'asserted from the run receipt' "
        "and an instrument finding for #5087 indicator 3.")

    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        raise SystemExit("s3-ablation: REFUSED — DEEPSEEK_API_KEY is unset; a keyless capture "
                         "extracts nothing, which measures the absence of a key (#3892)")
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        raise SystemExit("s3-ablation: REFUSED — TORTOISE_DB_URI is unset; S3 needs a searchable "
                         "backend (`uv run python tools/test_lane.py uri`)")

    from tests.eval.write_path.corpus import load_gold

    census = load_census()
    golds = {sid: load_gold(sid) for sid in sessions}
    order = [arm.strip() for arm in args.arm_order.split(",") if arm.strip()]
    stamp = head[:8]
    prefix = args.graph_prefix or f"s3ablate_{stamp}_{int(time.time())}"
    arms: dict[str, dict] = {}
    for arm in order:
        graph = f"{prefix}_{arm.lower()}"
        log(f"--- ARM {arm} — fresh graph {graph} ---")
        arms[arm] = run_arm(arm, graph, sessions, census, golds, log)
        data = arms[arm]
        log(f"  [{data['arm']}] real search_graph calls: {data['real_search_calls']} "
            f"(OFF arm must be 0)")
        log(f"  [{data['arm']}] created extraction nodes: {data['totals']['extraction_nodes']} "
            f"| aside Σlabel_counts delta: {data['totals']['label_instances']}")
        log(f"  [{data['arm']}] real facts: {data['real_facts']['survived']}"
            f"/{data['real_facts']['total']} planted units recovered")
        log(f"  [{data['arm']}] operator edges: {data['totals']['operator_edges']} "
            f"(graph-wide IMPL/NAND/SUPPORTS/MITIGATES/NEGATE/SUPERSEDE/INPUT)")
        log(f"  [{data['arm']}] model calls: {data['llm_calls']}\n")

    if "OFF" not in arms or "ON" not in arms:
        raise SystemExit("s3-ablation: REFUSED — both arms are required; a single-arm result is "
                         "never a verdict")

    result = decide(bars, floors, arms["OFF"], arms["ON"])
    off_recover = sum(1 for row in arms["OFF"]["sessions"] if not row["ok"])
    on_recover = sum(1 for row in arms["ON"]["sessions"] if not row["ok"])

    log("--- per-session table (created extraction nodes) ---")
    log(f"{'session':28s} {'OFF':>6s} {'ON':>6s} {'delta':>7s} {'OFF s3':>10s} {'ON s3':>10s}")
    for off_row, on_row in zip(arms["OFF"]["sessions"], arms["ON"]["sessions"]):
        log(f"{off_row['session_id']:28s} "
            f"{off_row['created']['extraction_nodes']:6d} "
            f"{on_row['created']['extraction_nodes']:6d} "
            f"{on_row['created']['extraction_nodes'] - off_row['created']['extraction_nodes']:7d} "
            f"{off_row['s3']['state']:>10s} {on_row['s3']['state']:>10s}")
    ratios = []
    for off_row, on_row in zip(arms["OFF"]["sessions"], arms["ON"]["sessions"]):
        off_n = off_row["created"]["extraction_nodes"]
        ratios.append((on_row["created"]["extraction_nodes"] / off_n) if off_n else None)
    defined = [value for value in ratios if value is not None]
    if defined:
        log(f"per-session ratio range: {min(defined):.3f} .. {max(defined):.3f} "
            f"(over {len(defined)} of {len(ratios)} sessions with a non-zero OFF denominator)")
    if result.get("concentration"):
        conc = result["concentration"]
        log(f"concentration (largest session's share of the total reduction): "
            f"{conc['share'] if conc['share'] is None else round(conc['share'], 3)} "
            f"(reduction total {conc['total_reduction']}, largest {conc['largest_session_reduction']})")

    log("")
    log(f"COUNTS: created extraction nodes OFF={result.get('created_off', arms['OFF']['totals']['extraction_nodes'])} "
        f"ON={result.get('created_on', arms['ON']['totals']['extraction_nodes'])}")
    log(f"RATIO: {result['ratio'] if result['ratio'] is None else round(result['ratio'], 4)}")
    log(f"ARM INTEGRITY: OFF {sum(1 for r in arms['OFF']['sessions'] if r['s3']['proven'])}/7 proven "
        f"(real search_graph calls {arms['OFF']['real_search_calls']}), "
        f"ON {sum(1 for r in arms['ON']['sessions'] if r['s3']['proven'])}/7 resolved "
        f"(real search_graph calls {arms['ON']['real_search_calls']})")
    log(f"REAL FACTS: OFF {arms['OFF']['real_facts']['survived']}/{arms['OFF']['real_facts']['total']} "
        f"ON {arms['ON']['real_facts']['survived']}/{arms['ON']['real_facts']['total']}")
    log(f"OPERATOR EDGES: OFF {arms['OFF']['totals']['operator_edges']} "
        f"ON {arms['ON']['totals']['operator_edges']}")
    log(f"COST: model calls OFF {arms['OFF']['llm_calls']} ON {arms['ON']['llm_calls']} "
        f"(ratio {result.get('cost_ratio')})")
    log(f"FAILED SESSIONS: OFF {off_recover} ON {on_recover}")
    verdict_line = result.get("verdict_text") or result["verdict"]
    if result["verdict"] in (VERDICT_REFUSED, VERDICT_ARM_INVALID):
        verdict_line = f"{result['verdict']}({result.get('why')})"
    elif result.get("qualifier"):
        verdict_line = f"{verdict_line} ({result['qualifier']})"
    log(f"VERDICT: {verdict_line}")
    log(f"WHY: {result.get('why')}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    print(f"s3-ablation: wrote {out_path.relative_to(ROOT)}")
    if args.json:
        print(json.dumps({"arms": arms, "result": result}, indent=2, default=str))
    return 0 if result["verdict"] not in (VERDICT_REFUSED, VERDICT_ARM_INVALID) else 3


if __name__ == "__main__":
    raise SystemExit(main())
