#!/usr/bin/env python3
"""Calibration harness — parallel exploration of extraction methods × models
× windows (epic #909). Relations are first-class (the tandem requirement);
the source-event connector captures events directly from structured sources
(PR/issue metadata) and CONNECTS them to conversation items.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import probe_extractor as pe  # noqa: E402

TANDEM_APPEND = """
REMINDER - EXTRACTION AND RELATIONS IN TANDEM (the most important rule):
every claim argues for/against something, so emit its IMPL/NAND relation with
the other item's id IN THE SAME PASS; every decision-event carries its
criteria claims (claims[] entries + IMPL relations targeting it); options
are the about_entities. Zero relations with more than 3 claims is a FAILED
extraction."""


def _staged_pass1(base: str) -> str:
    head = base.split("## Relations")[0] if "## Relations" in base else base
    return head + """
RULES: extract ONLY the items - events[] (Event nodes, eventKind per the
semantics above), claims[] (pointKind statement), process[], entities[]
(ontology vocab, near-miss flags), sources[]. DO NOT extract relations in
this pass. Output ONE JSON object with those keys only."""


STAGED_PASS2 = """You are the relation extractor (staged method, pass 2).
Here are the items already extracted (their ids are FIXED - reuse them).
Extract the RELATIONS between them: IMPL / NAND / MITIGATES, in tandem with
the argument structure: every claim's support/attack, every decision-event's
criteria claims, the deep-miss convention (support edge first, then the
tempering mitigation targeting the edge). Zero relations with more than 3
claims is a FAILED extraction. Output ONE JSON object: {"relations": [...]}."""


CORRECT_PASS = """You are the CORRECTION pass. The extraction below failed
deterministic validation with these errors:
{errors}
The items' ids are FIXED. Produce the FULL corrected stream (same keys:
events, claims, process, entities, relations, sources, nothing) fixing ONLY
the listed errors (add the missing source_ref, trim quotes to <=200, fix the
kinds, add the missing relations). Do not invent new items. One JSON object."""


STAGED_EMBEDDED_PASS2 = """You are the RELATION-EMBEDDING pass (staged method, pass 2).
Here are the items already extracted (ids are FIXED - reuse them). Your job:
re-emit the SAME items with their relations EMBEDDED INLINE - do NOT output
a separate relations[] array:
- claims[] gain "supports": ["id", ...] (what this claim argues FOR) and
  "attacks": ["id", ...] (what it argues AGAINST);
- events[] gain "criteria": ["claim-id", ...] (the claims justifying a
  decision-event) and "tempered_by": [{"claim_id", "target_edge_src",
  "target_edge_dst", "strength"}] (mitigations targeting an IMPL edge).
The deep-miss convention applies (support edge first, then the tempering
mitigation). Zero relations with more than 3 claims is a FAILED extraction.
Output ONE JSON object with the SAME keys as the input (events, claims,
process, entities, sources), with the inline relations added."""


def run_staged_embedded(edus, model, system):
    """Staged pipeline + embedded-relations pass 2: pass 1 extracts items,
    pass 2 re-emits the SAME items with relations embedded inline (the
    tandem schema). Pass-2 items REPLACE pass-1 so the inline relations
    survive the merge. Self-check: zero relations with >3 claims -> retry
    once inside the function (fix 2026-08-12)."""
    items = pe.extract_stream(model, edus, _staged_pass1(system))
    items = {k: v for k, v in items.items()
             if k in ("events", "claims", "process", "entities", "sources")}
    for _attempt in range(2):
        payload = json.dumps(items, indent=1)   # unwrapped: top-level keys
        try:
            rel_raw = pe._complete(model, STAGED_EMBEDDED_PASS2, payload)
        except Exception:
            rel_raw = None
        m = re.search(r"\{.*\}", rel_raw or "", re.S)
        if not m:
            continue
        try:
            fixed = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(fixed.get("items"), dict):
            fixed = fixed["items"]              # tolerate the wrapper echo
        merged = {k: fixed.get(k, items.get(k, [])) for k in
                  ("events", "claims", "process", "entities", "sources")}
        flat = flatten_embedded(merged)
        if len(flat.get("relations", [])) > 0 or len(items.get("claims", [])) <= 3:
            return flat
    return flatten_embedded(items)


def run_iterative(edus, model, system):
    """Staged + self-correct: run the deterministic validator, feed its
    errors back to the model for one corrective pass (design-space D1)."""
    stream = run_staged(edus, model, system)
    errors = validate_stream(stream)
    if not errors:
        return stream
    payload = json.dumps({"stream": stream, "errors": errors}, indent=1)
    fix = model.complete(system=CORRECT_PASS.format(errors="\n".join(errors[:8])),
                         user=payload)
    m = re.search(r"\{.*\}", fix or "", re.S)
    if m:
        try:
            corrected = json.loads(m.group(0))
            # keep fixed items; take corrected relations + any corrected fields
            stream["relations"] = corrected.get("relations", stream.get("relations", []))
            for c in corrected.get("claims", []):
                for orig in stream.get("claims", []):
                    if orig.get("id") == c.get("id"):
                        orig.update({k: v for k, v in c.items() if v})
            stream["events"] = corrected.get("events", stream.get("events", []))
        except Exception:
            pass
    return stream


# ── relations-embedded schema variant ──
EMBEDDED_SCHEMA = """
ALTERNATIVE OUTPUT SHAPE (embedded relations — use THIS): instead of a
separate relations[] array, embed the relations in the items:
  claims[]: {{"id", "edu_index", "content", "kind": "statement", "confidence",
             "about_entities", "source_ref", "quote",
             "supports": ["item-id"], "attacks": ["item-id"]}}
  events[]: {{"id", "edu_index", "content", "eventKind", "confidence",
             "about_entities", "source_ref", "quote",
             "criteria": ["claim-id"], "tempered_by": [{{"claim_id", "target_edge_src", "target_edge_dst", "strength"}}]}}
Relations are INLINE: every claim states what it supports/attacks; every
decision-event lists its criteria claims. The deep-miss convention applies
(support edge first, then the tempering mitigation). Zero relations with
more than 3 claims is a FAILED extraction.
"""


def flatten_embedded(stream: dict) -> dict:
    """Convert the embedded-relations shape to the flat relations[] for
    metrics + validation."""
    rels = list(stream.get("relations", []))
    for c in stream.get("claims", []):
        for t in c.get("supports", []) or []:
            rels.append({"src": c["id"], "dst": t, "op_type": "IMPL",
                         "direction": "unidirectional"})
        for t in c.get("attacks", []) or []:
            rels.append({"src": c["id"], "dst": t, "op_type": "NAND",
                         "direction": "unidirectional"})
    for e in stream.get("events", []):
        for crit in e.get("criteria", []) or []:
            rels.append({"src": crit, "dst": e["id"], "op_type": "IMPL",
                         "direction": "unidirectional"})
        for tm in e.get("tempered_by", []) or []:
            rels.append({"src": tm.get("claim_id"),
                         "target_edge": {"src": tm.get("target_edge_src"),
                                         "dst": tm.get("target_edge_dst"),
                                         "op_type": "IMPL"},
                         "op_type": "MITIGATES", "strength": tm.get("strength")})
    stream["relations"] = rels
    return stream


def run_staged(edus, model, system):
    items = pe.extract_stream(model, edus, _staged_pass1(system))
    items = {k: v for k, v in items.items()
             if k in ("events", "claims", "process", "entities", "sources")}
    payload = json.dumps({"items": items}, indent=1)
    rel_raw = model.complete(system=STAGED_PASS2, user=payload)
    m = re.search(r"\{.*\}", rel_raw or "", re.S)
    items["relations"] = json.loads(m.group(0)).get("relations", []) if m else []
    return items


EVK = {"decision", "occurrence", "deployment", "review", "extraction",
       "meeting", "experiment", "friction", "turn", "sessionCaptured",
       "AgentSession", "documentCreated", "roleCreated", "pointAdded",
       "humanApproval"}
VOCAB = {"core:concept", "core:standard", "core:other", "core:WorkItem",
         "core:document", "core:tool", "core:workflow", "core:Project",
         "core:tag", "core:user", "core:skill", "core:agent", "core:agreement",
         "core:strategy", "core:plan", "core:goal", "core:target",
         "product-strategy:product", "product-strategy:feature",
         "product-strategy:customer", "product-strategy:competitor",
         "product-strategy:customerSegment", "product-strategy:market",
         "product-strategy:requirement", "product-strategy:architecture",
         "dev:epic", "dev:issue", "dev:code", "dev:api", "dev:database",
         "dev:software", "dev:infrastructure", "dev:deployment",
         "dev:indicator", "marketing:campaign", "marketing:content",
         "marketing:channel", "marketing:audience", "marketing:keyword",
         "marketing:competitorContent", "pm:issue", "pm:sprint",
         "pm:kanbanBoard", "pm:card", "pm:milestone"}


def metrics(d: dict) -> dict:
    ev, cl, pr, en, rl = (len(d.get(k, [])) for k in
                          ("events", "claims", "process", "entities", "relations"))
    return {
        "events": ev, "claims": cl, "process": pr, "entities": en,
        "relations": rl,
        "decEv": sum(1 for e in d.get("events", [])
                     if e.get("eventKind") == "decision"),
        "minted": sum(1 for e in d.get("entities", [])
                      if e.get("kind") not in VOCAB),
        "badEvk": sum(1 for e in d.get("events", [])
                      if e.get("eventKind") not in EVK),
        "concepts": sum(1 for e in d.get("entities", [])
                        if e.get("kind") == "core:concept"),
        "mitigates": sum(1 for r in d.get("relations", [])
                         if r.get("op_type") == "MITIGATES"),
    }


def check_distribution_guards(stream: dict) -> list[str]:
    """Model-free guards: collapsed output = likely failure."""
    import collections as _c
    warnings = []
    ev = _c.Counter(e.get("eventKind") for e in stream.get("events", []))
    if ev and max(ev.values()) > 0.8 * sum(ev.values()):
        warnings.append(f"eventKind collapse: {dict(ev)}")
    n_claims = len(stream.get("claims", []))
    if n_claims > 5 and not stream.get("relations"):
        warnings.append("0 relations with >5 claims - support edges may be missing")
    if len(stream.get("events", [])) > 0 and not any(
            e.get("eventKind") == "decision" for e in stream.get("events", [])):
        # weak signal only: decisions are rare - do not warn on it alone
        pass
    return warnings


def validate_stream(stream: dict) -> list[str]:
    """Model-free schema validation of a stream (criteria v1)."""
    errors = []
    for e in stream.get("events", []):
        if not e.get("eventKind") or e["eventKind"] not in EVK:
            errors.append(f"event {e.get('id')}: bad eventKind {e.get('eventKind')!r}")
    for c in stream.get("claims", []):
        if (c.get("kind") or "statement") != "statement":
            errors.append(f"claim {c.get('id')}: kind must be statement, got {c.get('kind')!r}")
        if c.get("quote") and len(str(c["quote"])) > 200:
            errors.append(f"claim {c.get('id')}: quote >200")
        if not c.get("source_ref"):
            errors.append(f"claim {c.get('id')}: missing source_ref (R4)")
    for e in stream.get("entities", []):
        if e.get("kind") not in VOCAB:
            errors.append(f"entity {e.get('name')!r}: minted kind {e.get('kind')!r}")
    for r in stream.get("relations", []):
        if r.get("op_type") == "MITIGATES":
            if not r.get("target_edge") or r["target_edge"].get("op_type") != "IMPL":
                errors.append(f"relation {r.get('src')}: MITIGATES must target an IMPL edge")
            s = r.get("strength")
            if s is not None and not (0.10 <= float(s) <= 0.50):
                errors.append(f"relation {r.get('src')}: MITIGATES strength out of band")
    return errors


def source_events_from_pr(pr: dict) -> list[dict]:
    """A PR is an event source: the lifecycle IS structured metadata.
    Capture deterministically - no LLM needed; the CONNECTION step links
    these events to the conversation's points/objects."""
    events = []
    if pr.get("merged_at"):
        events.append({"id": f"e-pr{pr['number']}-merged", "eventKind": "deployment",
                       "content": f"PR #{pr['number']} merged: {pr.get('title', '')}",
                       "source_ref": pr.get("html_url", ""), "confidence": 1.0,
                       "capturedAt": pr["merged_at"]})
    elif pr.get("state") == "open":
        events.append({"id": f"e-pr{pr['number']}-opened", "eventKind": "review",
                       "content": f"PR #{pr['number']} open: {pr.get('title', '')}",
                       "source_ref": pr.get("html_url", ""), "confidence": 1.0})
    if pr.get("closed_at") and not pr.get("merged_at"):
        events.append({"id": f"e-pr{pr['number']}-closed", "eventKind": "occurrence",
                       "content": f"PR #{pr['number']} closed unmerged",
                       "source_ref": pr.get("html_url", ""), "confidence": 1.0,
                       "capturedAt": pr["closed_at"]})
    return events


def connect_events_to_items(events: list[dict], items: dict) -> list[dict]:
    """The CRITICAL step: connect source events to conversation entities by
    name resolution (the event's title tokens vs the session's entities)."""
    session_entities = {e["name"].lower(): e for e in items.get("entities", [])}
    conns = []
    for ev in events:
        tokens = set((ev.get("content") or "").lower().split())
        hits = [name for name in session_entities
                if any(len(t.strip(".,#")) >= 4 and t.strip(".,#") in name.lower()
                       for t in tokens)]
        conns.append({"event": ev["id"], "content": ev["content"][:60],
                      "connects_to": hits[:4]})
    return conns


# ── Experiments ledger (the calibration loop's memory) ────────────────────
EXPERIMENTS_FILE = Path(__file__).resolve().parent / "experiments.jsonl"


def log_experiment(entry: dict) -> None:
    """Append one experiment cell to the ledger (thread-safe enough for the
    harness's sequential cells). Every run is comparable and cumulative."""
    with open(EXPERIMENTS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def report_experiments(filters: dict | None = None) -> None:
    """Print the experiments table (filter: window/method/model)."""
    rows = []
    if EXPERIMENTS_FILE.exists():
        for line in EXPERIMENTS_FILE.read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if filters and any(r.get(k) != v for k, v in filters.items()):
                continue
            rows.append(r)
    hdr = f"{'ts':<17} {'window':<11} {'method':<16} {'model':<9} {'rl':>4} {'decEv':>5} {'mit':>4} {'mint':>5} {'dur':>6} {'status':<12}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r.get('ts','')[:16]:<17} {str(r.get('window','')):<11} "
              f"{str(r.get('method','')):<16} {str(r.get('model','')):<9} "
              f"{r.get('relations',0):>4} {r.get('decEv',0):>5} {r.get('mitigates',0):>4} "
              f"{r.get('minted',0):>5} {r.get('duration_s',0):>6.0f} {str(r.get('status','')):<12}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibration harness")
    ap.add_argument("--windows", nargs="+", default=[])
    ap.add_argument("--methods", nargs="+", default=["single", "tandem", "staged"])
    ap.add_argument("--models", nargs="+", default=["deepseek-flash"])
    ap.add_argument("--pr-json", default=None)
    ap.add_argument("--report", action="store_true",
                    help="print the experiments ledger table and exit")
    args = ap.parse_args()
    if args.report:
        report_experiments()
        return 0

    windows = args.windows or [
        ("w3-design", "/tmp/window3-transcript.txt"),
        ("w4-impl", "/tmp/window2b-transcript.txt"),
        ("w5-workflow", "/tmp/window5-transcript.txt"),
    ]
    if windows and ":" in windows[0]:
        windows = [tuple(w.split(":", 1)) for w in windows]
    if not all(Path(t).exists() for _, t in windows):
        print("windows not found - pass --windows explicitly", file=sys.stderr)
        return 1

    from tests.model_adapters import MODELS

    header = (f"{'window':<11} {'method':<8} {'model':<9} {'ev':>3} {'cl':>3} "
              f"{'pr':>3} {'en':>4} {'rl':>3} {'decEv':>5} {'mit':>3} "
              f"{'mint':>4} {'badEvk':>6}")
    print(header)
    print("-" * len(header))
    for wname, wpath in windows:
        edus = pe.parse_transcript(Path(wpath).read_text())
        for method in args.methods:
            for model_name in args.models:
                stream = None
                _last_err = ""
                for attempt in range(3):
                    try:
                        model = MODELS[model_name]()
                        model.max_tokens = 32000
                        model.temperature = 0.2
                        system = pe.EXTRACTION_SYSTEM + (
                            TANDEM_APPEND if method == "tandem" else "")
                        if method == "staged":
                            stream = run_staged(edus, model, system)
                        elif method == "iterative":
                            stream = run_iterative(edus, model, system)
                        elif method == "embedded":
                            stream = pe.extract_stream(model, edus, system + EMBEDDED_SCHEMA)
                            stream = flatten_embedded(stream)
                        elif method == "staged-embedded":
                            stream = run_staged_embedded(edus, model, system)
                        else:
                            stream = pe.extract_stream(model, edus, system)
                        break
                    except Exception as _e:
                        stream = None
                        _last_err = f"{type(_e).__name__}: {str(_e)[:60]}" 
                if stream is not None:
                    m = metrics(stream)
                    print(f"{wname:<11} {method:<8} {model_name:<9} "
                          f"{m['events']:>3} {m['claims']:>3} {m['process']:>3} "
                          f"{m['entities']:>4} {m['relations']:>3} {m['decEv']:>5} "
                          f"{m['mitigates']:>3} {m['minted']:>4} {m['badEvk']:>6}")
                    log_experiment({
                        "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                        "window": wname, "method": method, "model": model_name,
                        "config": {"chunk_size": 6, "deadline_s": 600},
                        **m, "status": "ok"})
                else:
                    print(f"{wname:<11} {method:<8} {model_name:<9} "
                          f"FAILED after 3 attempts ({_last_err})")
                    log_experiment({
                        "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                        "window": wname, "method": method, "model": model_name,
                        "config": {"chunk_size": 6, "deadline_s": 600},
                        "status": "failed_after_3_attempts",
                        "error": _last_err})

    if args.pr_json:
        pr = json.loads(Path(args.pr_json).read_text())
        events = source_events_from_pr(pr)
        print("\n=== SOURCE-EVENT CONNECTOR (PR as the event source) ===")
        for ev in events:
            print(f"  captured: {ev['id']} ({ev['eventKind']}) - {ev['content'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
