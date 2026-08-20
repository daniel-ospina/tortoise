#!/usr/bin/env python3
"""Probe extractor — the calibration-loop runner (epic #909, window #2).

Takes an utterance-tagged session transcript and runs the R1-R9 extraction
rubric as a single LLM pass: classification (decision/event/claim/process)
+ entity extraction TYPED BY THE ONTOLOGY (core + expansion-pack kinds)
+ relations (IMPL / NAND / MITIGATES). Emits the typed stream
{decisions[], events[], claims[], entities[], relations[]} — the shape the
value-first extractor (slice 6b) will produce, so the owner reviews the
SYSTEM's output directly and the loop calibrates the rubric.

This is the bootstrap probe: the rubric prompt here is the lineage of the
future value_brief + value_extractor prompts.
"""
from __future__ import annotations  # noqa: I001

import argparse
import json
import re
import sys
from pathlib import Path

# ── Ontology vocabulary + SEMANTICS — compiled at runtime from the merged
# packs (PackRegistry, the canonical source — same lineage as the value brief)
import yaml
from pathlib import Path as _Path


def _compile_vocab(packs_dir: _Path) -> dict:
    """Compile {namespace: {kind: {description, nearMisses, examples}}} from
    the installed packs (criteria v1 §2 — semantics-first entity typing)."""
    from tortoise.pack_registry import PackRegistry
    reg = PackRegistry(packs_dir)
    reg.load_all()
    # namespace → manifest file (pack dir names may differ from namespaces)
    ns_to_file = {}
    for mf in sorted((packs_dir).glob("*/manifest.yaml")):
        d = yaml.safe_load(mf.read_text()) or {}
        if d.get("namespace"):
            ns_to_file[d["namespace"]] = mf
    vocab: dict[str, dict] = {}
    for ns, pack in sorted(reg.packs.items()):  # noqa: B007
        ont = {}
        raw = yaml.safe_load(ns_to_file[ns].read_text()) or {}
        kind_defs = (raw.get("ontology") or {}).get("kindDefs") or {}
        for kind, spec in kind_defs.items():
            ont[kind] = {
                "description": spec.get("description", ""),
                "nearMisses": spec.get("nearMisses", []),
                "examples": spec.get("examples", [])[:1],
            }
        vocab[ns] = ont
    return vocab


def _render_kind_semantics(vocab: dict, ns: str, kinds: list[str]) -> str:
    lines = []
    for k in kinds:
        spec = vocab.get(ns, {}).get(k, {})
        desc = spec.get("description") or ""
        nm = spec.get("nearMisses") or []
        ex = spec.get("examples") or []
        parts = [f"{ns}:{k}"]
        if desc:
            parts.append(f"— {desc}")
        if nm:
            parts.append(f"[confusable with: {', '.join(nm)}]")
        if ex:
            parts.append(f"e.g. {ex[0]!r}")
        lines.append("  " + " ".join(parts))
    return "\n".join(lines)


# Core kind semantics (ONTOLOGY §5 + canonical descriptions)
CORE_OBJECT_KINDS = "Project, WorkItem, document, tag, user, skill, tool, agent, workflow, agreement, standard, concept, other"
CORE_SUBJECT_KINDS = "organization, team, role, legalPerson, naturalPerson, other"
CORE_SEMANTICS = {
    "Project": "A top-level initiative or product line",
    "WorkItem": "A unit of work (epic, issue, task)",
    "tool": "A tool, CLI, or utility used in the work",
    "workflow": "A reusable procedural sequence",
    "document": "A document artifact",
    "standard": "A standard, spec, or canonical reference",
    "concept": "An abstract idea or model — INCLUDING the system's own concepts (points, decisions, claims, options, criteria, lifecycle, the state-centric model). Sessions ABOUT the ontology produce concepts; do NOT force them into other/architecture/standard.",
    "other": "No fitting kind — the EXPLICIT uncertain/other bucket (never forced)",
}
NEAR_MISS_ENTITY_KINDS = (
    "product-strategy:useCase, product-strategy:userJourney, "
    "product-strategy:jobToBeDone, product-strategy:valueProposition, "
    "dev:requirement, dev:bug, dev:technicalDebt, marketing:contentBrief, "
    "marketing:contentPerformance, pm:estimate, pm:retrospective"
)
POINT_KINDS = "decision, vision, strategy, plan, goal, target (decisions) · statement, observation, hypothesis (claims) · event (events)"

R9_CUE_TAXONOMY = (
    "it's an estimate, decide with real telemetry, a positioning tension not "
    "structural, the caveat is, only if, gated on, the one swing variable is, "
    "only achievable because, the leading indicator is, preliminary, "
    "watch-gate not a statistical test, none would let it be built as-is, "
    "still to run before the gate is green, real but not transformative"
)


def _build_extraction_system(packs_dir: _Path) -> str:
    vocab = _compile_vocab(packs_dir)
    ps = sorted((vocab.get("product-strategy") or {}).keys())
    dev = sorted((vocab.get("dev") or {}).keys())
    mkt = sorted((vocab.get("marketing") or {}).keys())
    pm = sorted((vocab.get("pm") or {}).keys())
    core_lines = "\n".join(
        f"  core:{k} — {desc}" for k, desc in CORE_SEMANTICS.items())

    return f"""You are the value-first extractor (rubric version value@0.3.0-draft — criteria v1, audited against ONTOLOGY v3.6 + the merged expansion packs; the window-1 R1-R9 rubric + the audit-expanded cue taxonomy).

INPUT: a numbered agent-work session transcript (EDUs). OUTPUT: the typed extraction stream as ONE JSON object — no prose before or after. Stream schema (window-1): decisions / events / claims / process / entities / relations / sources / nothing.

## Classification axis (what each utterance IS) — criteria v1 §1
| Class | Ontology mapping | Definition |
|---|---|---|
| decision | **Event node** (eventKind `decision`, aboutObject → the option(s) it resolved) — the timeline record of the commitment; the RESOLUTION is expressed as lifecycle writes on the state objects (chosen promoted, alternatives deprecated) + the criteria claims IMPL-ing them. NO decision Points (state-centric). | COMMISSIVE ∧ product-knowledge-bearing (R1∧R3). Cues: decided, chose, agreed to, we're going with, the ruling is, default to, ship X first, reject Y, we will / I will (with subject agentivity ∧ the conjunction). NEIGHBOR TURNS: agreement/restatement in the following turns supports the decision reading. EVENTS CARRY about_entities = the options (incl. rejected). |
| claim | Point pointKind ∈ {{statement, observation, hypothesis}} | ASSERTIVE stative/gnomic. Cues: is, costs, fails, implies, means, shows, requires, depends on, the cause is, the risk is + quantified facts. Do NOT trust surface copulas alone — check the predicate's aspect. |
| event | **Event NODE** — NEVER a Point (issue #1013). EVENTKIND SEMANTICS: `occurrence` = anything that happened (default — implementation work, fixes, merges, filings); `deployment` = SHIPPED TO PRODUCTION only; `review` = code review / evaluation / research passes; `extraction` = extraction runs; `decision` = the timeline record of a commitment; `turn` = CAPTURE-PATH turns ONLY — NEVER use it for extracted events. VARY the eventKind by content — emitting the same kind for every event is a failure (iteration 5). | ASSERTIVE past-perfective. Cues: fixed, shipped, merged, deployed, closed, ran, measured, filed, created ("did X"). "Implemented X" / "PR merged" / "issue filed" → occurrence (or review/decision per the semantics), NEVER deployment. |
| process | NOT a graph point (R3) — dropped with a logged reason | Work/governance commitment. Cues: let me X, I'll fix X now, validate on 2 windows first, record this on the issue. |
| nothing | no item — rejected WITH a reason | Boilerplate, dispatch/instruction text, tool dumps, headers, file paths, module names, HTTP codes, git refs. |

RULES: "should" = recommendation — NEVER decision. Atomicity (R2): "A AND B AND C" splits. One class per EDU.

THE CLAIM GATE (iteration 6 — the corrected discriminator): glue = talk ABOUT THE CONVERSATION; facts = talk ABOUT THE WORK. Classify as NOTHING (with reason): acknowledgments ("agreed", "yes exactly", "ok"), agreements, recaps/summaries of what was said ("so the idea is...", "hypothesis consolidated"), meta-talk about the conversation itself ("that is the core hypothesis to test"). Classify as CLAIM — do NOT gate these — any assertion of fact about the WORK or the domain, even in narration form: "X is already merged into main", "the test fails on exception propagation", "found a pre-existing bug: ...", "the fixture explains it", "all entity props persist". Classify as EVENT when past-perfective: "the run hung", "PR merged", "the extraction ran". Classify as PROCESS only pure intentions/instructions: "I will fix X", "let me check Y", "write the test suite".

## Entity axis (what entities are mentioned) — CLOSED vocab WITH semantics (criteria v1 §2)
Type every entity mention against the kind SEMANTICS below — never name-matching, never minted kinds. Prefer the MOST SPECIFIC kind; near-miss (⚠) → the closest kind WITH the flag and the confusable sibling named; no fit or low confidence → core:other EXPLICITLY + a pack-proposal note (never forced nearest-kind).
CONFUSABLE GUARD: product-strategy:architecture is for the DESIGN/STRUCTURE of a solution ("the four-node chain architecture") — NOT for abstract models ("the state-centric model" → core:concept), NOT for implementation details ("Object MERGE key", "rate bucket" → dev:code or core:concept), NOT for work items ("quota fix" → dev:issue is wrong — it is a work item → core:WorkItem).

CORE:
{core_lines}

PRODUCT-STRATEGY ({', '.join(ps)}):
{_render_kind_semantics(vocab, 'product-strategy', ps)}

DEV ({', '.join(dev)}):
{_render_kind_semantics(vocab, 'dev', dev)}

MARKETING ({', '.join(mkt)}):
{_render_kind_semantics(vocab, 'marketing', mkt)}

PM ({', '.join(pm)}):
{_render_kind_semantics(vocab, 'pm', pm)}

NEAR-MISS entity kinds (pointKinds pressed into entity service — ALWAYS ⚠-flagged): {NEAR_MISS_ENTITY_KINDS}
NOT entities: relations (IMPL/NAND/MITIGATES), operators, edges, file paths/module names (quota.py), HTTP codes, git refs/branches, REFERENCE NUMBERS (masked as [REF] in the transcript — never entities; the artifact itself may be an entity, the reference number is not), version tags, or generic plural mentions ("open issues", "live epics", "competitors" — only specific named instances). WORK ITEMS that are not a named issue/epic ("quota fix", "the pricing refactor") → core:WorkItem, never dev:issue (dev:issue is for NAMED issues like "issue #953" — references are masked as [REF]).
PACK PROPOSALS: recurring mentions with no kind (test, model, session, ...) are collected in entities[] with near_miss: "pack proposal: <kind>" — never minted.

## EXTRACTION AND RELATIONS IN TANDEM (the deep-miss convention — the most important rule)
Relations are NOT an optional section — they are extracted TOGETHER with the items they connect, in the SAME pass, with IDs that resolve within this pass. The procedure for every item:

1. For each CLAIM: ask "what does this argue for/against?" → emit the IMPL (supports) or NAND (attacks) relation in the SAME breath, referencing the other item's id. A claim that argues for something with NO relation is a MISSED relation.
2. For each DECISION-EVENT: extract its CRITERIA IN TANDEM — the claims that justify it become claims[] entries AND IMPL relations targeting the decision-event. A decision-event with no criteria claims is incomplete.
3. For each OPTION (about_entities): the decision-event's options are the entities it resolves between — the rejected option is as important as the chosen one.
4. MITIGATES (R9 — PRIMARY): targets the EDGE {{src, dst, op_type: IMPL}}, NEVER a point; bias 0.10-0.50; quote. THE DEEP-MISS CONVENTION: extract the support edge FIRST (steps 1-2), THEN look for the tempering claim that attaches to it ("it's an estimate", "gated on", "preliminary", "real but not transformative" — cue taxonomy: {R9_CUE_TAXONOMY}).
5. Canonical case (emit when instantiated): X "it's cheap" IMPL A; Z "we can raise the price" MITIGATES [X→A]; Y "customers aren't price-sensitive" IMPL Z.
6. NAND: unidirectional by default (extraction-emitted); bidirectional only for explicit mutual restatement.
7. Self-check before finishing: every claim in the stream participates in ≥1 relation OR is an isolated factual observation (rare — say why); every decision-event has ≥1 criteria relation; count your relations — zero relations with more than 3 claims is a FAILED extraction.

## Confidence rubric
0.9+ explicit unambiguous · 0.7-0.9 clear, hedged/second-hand · <0.7 flag low. Every decision/claim carries source_ref (R4) + quote ≤200.

## Output schema (ONE JSON object, nothing else)
{{
  "events": [{{"id": "e1", "edu_index": int, "content": str, "eventKind": "decision|occurrence|deployment|review|extraction|turn", "atomicity": true, "confidence": float, "about_entities": ["option-name"], "source_ref": str, "quote": str}}],
  "claims": [{{"id": "c1", "edu_index": int, "content": str, "kind": "statement", "confidence": float, "about_entities": ["name"], "source_ref": str, "quote": str}}],
  "process": [{{"id": "p1", "edu_index": int, "content": str, "reason": "R3 — logged drop reason"}}],
  "entities": [{{"name": str, "kind": "ns:kind", "near_miss": "⚠ confusable: <sibling> | pack proposal: <kind> | null", "edu_indices": [int]}}],
  "relations": [{{"src": "id", "dst": "id", "op_type": "IMPL|NAND", "direction": "unidirectional|bidirectional", "quote": str}}, {{"src": "id", "target_edge": {{"src": "id", "dst": "id", "op_type": "IMPL"}}, "op_type": "MITIGATES", "strength": 0.1-0.5, "quote": str}}],
  "sources": [{{"id": "s1", "url": str, "sourceKind": str, "refs": ["d1", "c2"]}}],
  "nothing": [{{"edu_index": int, "reason": str}}]
}}
"""


import re as _re  # noqa: E402

_REF_RE = _re.compile(
    r"\b(?:PR|docs PR|issue|epic)?\s*#\d{2,6}\b|"
    r"\bPR\s+#?\d{2,6}\b|"
    r"\b(?:issue|epic)\s+#?\d{2,6}\b|"
    r"\bv\d+\.\d+(?:\.\d+)?\b"
)


def _mask_references(text: str) -> str:
    """S0 pre-filter (iteration 3): mask reference tokens (PR #999,
    #1017, v3.7) so the model never extracts them as entities — the
    artifact may be an entity, the reference number never is."""
    return _REF_RE.sub("[REF]", text)


# Module-level default system — built at import so the harness can use it
# without running main(); main() re-builds with the worktree's packs.
_DEFAULT_PACKS = _Path(__file__).resolve().parent.parent / "packs"
try:
    EXTRACTION_SYSTEM = _build_extraction_system(_DEFAULT_PACKS)
except Exception:
    EXTRACTION_SYSTEM = _build_extraction_system(_DEFAULT_PACKS) if _DEFAULT_PACKS.exists() else ""


def parse_transcript(text: str) -> list[dict]:
    edus = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+):\s*(\w+):\s*(.*)$", line)
        if not m:
            continue
        edus.append({"index": int(m.group(1)), "role": m.group(2),
                     "text": _mask_references(m.group(3).strip())})
    return edus


def build_user_prompt(edus: list[dict]) -> str:
    lines = [f"{e['index']}: {e['role']}: {e['text']}" for e in edus]
    return "TRANSCRIPT (numbered EDUs):\n" + "\n".join(lines)


_EMPTY = {"events": [], "claims": [], "process": [], "entities": [],
          "relations": [], "sources": [], "nothing": []}


def _complete(model, system: str, user: str, deadline_s: int = 600) -> str:
    """Wall-clock-bounded completion: requests' timeout only guards between
    bytes — a slow stream can hang for minutes. A deadline makes the call
    fail fast so the harness retries (cheap on flash). No token caps — the
    model decides its output length (owner, 2026-08-12)."""
    import threading
    box = {}

    def _run():
        box["resp"] = model.complete(system=system, user=user)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=deadline_s)
    if t.is_alive():
        raise TimeoutError(f"model call exceeded {deadline_s}s deadline")
    return box.get("resp")


def _parse_json_block(response: str) -> dict:
    m = re.search(r"\{.*\}", response or "", re.S)
    if not m:
        raise ValueError("model returned no JSON block")
    block = m.group(0)
    for cut in (None, -1, -2, -3, -5, -10):
        try:
            return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError:
            continue
    raise ValueError("model returned unparseable JSON block")


def extract_stream(model, edus: list[dict], system: str = None,  # noqa: RUF013
                   chunk_size: int = 6) -> dict:
    """Extract in CHUNKS (window segmentation): full-window outputs exceed
    provider output limits (measured truncation at ~18.6K chars). Each chunk
    gets a unique id prefix so item ids stay unique after the merge."""
    if chunk_size <= 0 or len(edus) <= chunk_size:
        return _parse_json_block(_complete(
            model, system or EXTRACTION_SYSTEM, build_user_prompt(edus)))

    merged = {k: list(v) for k, v in _EMPTY.items()}
    seen_entities = {}
    for start in range(0, len(edus), chunk_size):
        chunk = edus[start:start + chunk_size]
        prefix = f"ch{start // chunk_size}"
        chunk_system = (system or EXTRACTION_SYSTEM) + (
            f"\nCHUNK ID PREFIX: prefix every item id with '{prefix}-' "
            f"(e.g. c1 -> {prefix}-c1); relations/criteria/options references "
            f"use the prefixed ids.")
        partial = None
        for _attempt in range(3):  # per-chunk retry — provider flakiness
            try:
                partial = _parse_json_block(_complete(
                    model, chunk_system, build_user_prompt(chunk)))
                break
            except Exception as _e:
                partial = None
        if partial is None:
            # Skip the chunk with a warning — partial extraction beats a dead
            # cell (calibration harness resilience, 2026-08-12).
            print(f"WARNING: chunk {prefix} failed after 3 attempts — skipped",
                  file=sys.stderr)
            continue
        for k in ("events", "claims", "process", "relations", "sources", "nothing"):
            merged[k].extend(partial.get(k, []) or [])
        for e in partial.get("entities", []) or []:
            key = (e.get("name", ""), e.get("kind", ""))
            if key in seen_entities:
                seen_entities[key].setdefault("edu_indices", []).extend(
                    e.get("edu_indices", []) or [])
            else:
                seen_entities[key] = e
                merged["entities"].append(e)
    return merged


def render(stream: dict, edus: list[dict]) -> str:
    out = []
    out.append("# Extraction output — window #2 (w2-op, operational)")
    out.append("")
    out.append(f"EDUs: {len(edus)}")
    out.append("")

    if stream.get("events"):
        out.append(f"## Events ({len(stream['events'])})")
        for e in stream["events"]:
            out.append(f"- **{e['id']}** (edu {e['edu_index']}) {e['content'][:120]}")
    if stream.get("claims"):
        out.append(f"## Claims ({len(stream['claims'])})")
        for c in stream["claims"]:
            ents = ", ".join(c.get("about_entities") or []) or "—"
            out.append(f"- **{c['id']}** (edu {c['edu_index']}, {c.get('kind')}) "
                       f"{c['content'][:120]} | entities: {ents}")
    if stream.get("process"):
        out.append(f"## Process (R3 — dropped with log, NOT graph points) ({len(stream['process'])})")
        for p in stream["process"]:
            out.append(f"- {p['id']} (edu {p['edu_index']}) {p['content'][:100]}")
    if stream.get("entities"):
        out.append(f"## Entities ({len(stream['entities'])}) — ontology-typed")
        for e in stream["entities"]:
            out.append(f"- **{e['name']}** → `{e['kind']}` (edus {e.get('edu_indices')})")
    if stream.get("relations"):
        out.append(f"## Relations ({len(stream['relations'])})")
        for r in stream["relations"]:
            if r.get("op_type") == "MITIGATES":
                t = r.get("target_edge", {})
                out.append(f"- {r['src']} **MITIGATES** [{t.get('src')}→{t.get('dst')}] "
                           f"strength {r.get('strength')}")
            else:
                out.append(f"- {r['src']} **{r['op_type']}** {r['dst']} "
                           f"({r.get('direction', '')})")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe extractor — calibration loop runner")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--out", help="save the typed stream JSON here")
    ap.add_argument("--render", help="save the rendered review output here")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tests.model_adapters import MODELS  # noqa: PLC0415, RUF100

    edus = parse_transcript(Path(args.transcript).read_text())
    model = MODELS[args.model]()
    model.max_tokens = args.max_tokens
    model.temperature = 0.2

    global EXTRACTION_SYSTEM
    EXTRACTION_SYSTEM = _build_extraction_system(
        _Path(__file__).resolve().parent.parent / "packs")
    stream = extract_stream(model, edus)
    # Distribution guard (iteration 5): collapsed output = likely failure —
    # >80% of events sharing one eventKind, or 0 relations with >5 claims,
    # or 0 decision events in a decision-bearing window.
    import collections as _c
    ev = _c.Counter(e.get("eventKind") for e in stream.get("events", []))
    if ev and max(ev.values()) > 0.8 * sum(ev.values()):
        print(f"WARNING: eventKind collapse — {dict(ev)} (iteration-5 guard)", file=sys.stderr)
    n_claims = len(stream.get("claims", []))
    if n_claims > 5 and not stream.get("relations"):
        print("WARNING: 0 relations with >5 claims — support edges may be missing", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(json.dumps(stream, indent=2) + "\n")
    rendered = render(stream, edus)
    if args.render:
        Path(args.render).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
