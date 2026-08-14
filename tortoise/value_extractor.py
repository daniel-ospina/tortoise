"""Value extractor — the two-process production pipeline (epic #909, 2026-08-13).

Process 1: conversation → summary (session metadata + state/decisions/logic/
issues). Process 2: summary → graph delta (NEW / CHANGED / EXISTS + connections)
against the existing graph, with a DETERMINISTIC pre-match for scale.

The deterministic enforcer (validate_summary) applies the criteria rules to
the summary stream before it is committed. This module is the production home
of the calibration loop's measured design (tools/summary_extractor.py +
tools/calibration_harness.py are its dev-tool lineage).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# ── The value brief: compiled vocab + semantics (the #954 contract) ─────────

def compile_value_brief(packs_dir: Path | str | None = None) -> dict:
    """The closed vocabulary + kind semantics from the installed packs.
    The same source the prompts and the enforcer validate against."""
    from tortoise.pack_registry import PackRegistry
    import yaml
    packs_dir = Path(packs_dir) if packs_dir else \
        Path(__file__).resolve().parent.parent / "packs"
    reg = PackRegistry(packs_dir)
    reg.load_all()
    ns_files = {}
    for mf in packs_dir.glob("*/manifest.yaml"):
        d = yaml.safe_load(mf.read_text()) or {}
        if d.get("namespace"):
            ns_files[d["namespace"]] = mf
    kinds = {}
    for ns, _ in reg.packs.items():
        raw = yaml.safe_load(ns_files[ns].read_text()) or {}
        kd = (raw.get("ontology") or {}).get("kindDefs") or {}
        for k, spec in kd.items():
            kinds[f"{ns}:{k}"] = {
                "description": spec.get("description", ""),
                "nearMisses": spec.get("nearMisses", []),
            }
    core = {
        "core:concept": "An abstract idea or model (incl. the system's own concepts)",
        "core:standard": "A standard, spec, or canonical reference",
        "core:document": "A document artifact",
        "core:tool": "A tool, CLI, or utility",
        "core:workflow": "A reusable procedural sequence",
        "core:WorkItem": "A unit of work",
        "core:other": "No fitting kind - the explicit uncertain bucket",
    }
    return {**core, **kinds}


# ── Process 1: the summary pass ─────────────────────────────────────────────

SUMMARY_SYSTEM = """You are the session SUMMARIZER for the Tortoise epistemic memory (state-centric model).

Read the WHOLE conversation (a single agent work session). Do NOT classify
sentences - synthesize the session into the epistemic structure. Value is the
filter: procedural chatter, acknowledgments, recaps are DROPPED.

Output ONE JSON object:
{
  "session": {"type": "design|operational|research", "summary": str <=120 words},
  "state": [{"name": str, "objectKind": str, "status": "retained|discarded|proposed|changed",
             "change": str|null, "options": [{"name", "kind", "retained", "note"}], "edu_refs": [int]}],
  "decisions": [{"content": str (posterity wording), "why": str, "for": [str],
                 "against": [str], "mitigations": [str], "options": [str],
                 "chosen": str|null, "sources": [int]}],
  "issues": [{"id": str|null, "status": "created|completed|referenced", "content": str, "edu_refs": [int]}],
  "logic": [{"point": str (posterity wording), "supports": str|null,
             "opposes": str|null, "sources": [int]}]
}
STATE = what IS after this session (objects per the ontology, incl. OPTIONS
retained AND discarded). DECISIONS = commitments resolving the confidence
dynamics, with the WHY + FOR/AGAINST + MITIGATIONS, in posterity wording.
ISSUES = created/completed referenced (precise lifecycle syncs from GitHub).
LOGIC = the durable beliefs/knowledge with their sources (edu_refs as
supporting evidence). Empty sections are fine - extract-nothing is valid.
Output ONLY the JSON object."""


# ── Process 2: the grounding pass ───────────────────────────────────────────

GROUNDING_SYSTEM = """You are the GROUNDING pass: connect a session summary to the EXISTING graph state.

Input: (a) the session summary, (b) the existing graph state (each item has
an explicit "id").

IDENTITY MATCHING: match summary items to existing items by NAME (+ kind on
collision). If a summary item matches an existing item by name -> CHANGED
(supersedes = THE MATCHED EXISTING ID, never itself) or EXISTS (unchanged,
connect only). If NO name match -> NEW. If a summary item describes prior
state the existing graph lacks -> CHANGED with supersedes = the closest prior
item id, or NEW+deprecated.

Output ONE JSON object:
{"delta": [{"item": str, "kind": "state|decision|logic|issue",
            "action": "NEW|CHANGED|EXISTS", "lifecycle": "created|superseded|deprecated|promoted|unchanged",
            "supersedes": "existing-id|null",
            "connections": [{"to": "existing-id", "edge": "aboutObject|IMPL|NAND|MITIGATES|extractedFrom|references", "note": str|null}]}]}
Be precise and minimal."""


# ── The deterministic enforcer (the #956 contract) ─────────────────────────

EVENT_KINDS = {"decision", "occurrence", "deployment", "review", "extraction",
               "meeting", "experiment", "friction", "turn", "sessionCaptured",
               "AgentSession", "documentCreated", "roleCreated", "pointAdded",
               "humanApproval"}


def validate_summary(summary: dict, vocab: dict | None = None) -> list[str]:
    """Deterministic validation of the summary stream (the enforcer)."""
    errors = []
    for d in summary.get("decisions", []) or []:
        if not d.get("content"):
            errors.append("decision: missing content")
        if not d.get("why"):
            errors.append(f"decision {d.get('content','')[:30]}: missing why (the logic)")
        if len(str(d.get("content", ""))) > 1000:
            errors.append("decision: content >1000")
    for s in summary.get("state", []) or []:
        if not s.get("name"):
            errors.append("state: missing name")
    for l in summary.get("logic", []) or []:
        if not l.get("point"):
            errors.append("logic: missing point")
        if not l.get("sources"):
            errors.append(f"logic {l.get('point','')[:30]}: missing sources (R4)")
    return errors


def check_guards(summary: dict) -> list[str]:
    """Distribution guards: collapse / emptiness warnings."""
    warns = []
    n_dec = len(summary.get("decisions", []) or [])
    n_state = len(summary.get("state", []) or [])
    n_logic = len(summary.get("logic", []) or [])
    if n_state == 0 and n_dec == 0 and n_logic == 0:
        warns.append("empty summary for a non-empty session - possible extraction failure")
    if n_dec > 0 and n_logic == 0:
        warns.append("decisions without logic (the why) - under-extraction")
    return warns


# ── Step 2: graph construction (the structure pass) ─────────────────────────
# The owner's refinement (2026-08-13): first derive the LOGIC (the summary,
# semantics), then CONSTRUCT the graph structure — arguments become statement
# Points wired to the options they argue about (aboutObject) and the decisions
# they support/oppose (IMPL/NAND); mitigations target the support edges. The
# state objects' confidence is derived from these attached points.

CONSTRUCT_SYSTEM = """You are the GRAPH CONSTRUCTOR. Turn a session summary into
the epistemic graph structure. The arguments are the confidence engine: every
for/against/mitigation must become a Point wired to its targets.

Input: the session summary (state/decisions/logic/issues).
Output (ONE JSON object — the derived-commit stream):
{
  "entities": [{"name", "kind", "passes_frequency_gate": true}],   # state objects AND their options
  "events": [{"id": "ev_<sha>", "eventKind": "decision|occurrence", "content",
              "about_entities": [chosen-option], "source_ref": "session.md"}],
  "points": [{"id": "pt_<sha>", "content", "pointKind": "statement",
              "about_entities": [the option this argument argues about],
              "source_ref": "session.md", "quote": ""}],
  "operators": [
    {"src": "pt-id", "dst": "ev-id", "op_type": "IMPL", "direction": "unidirectional"},   # an argument FOR a decision
    {"src": "pt-id", "dst": "ev-id", "op_type": "NAND", "direction": "unidirectional"},   # an argument AGAINST
    {"src": "pt-id", "target_edge": {"src": "pt-id", "dst": "ev-id", "op_type": "IMPL"},   # a mitigation tempers a support edge
     "op_type": "MITIGATES", "strength": 0.1-0.5}
  ]
}
RULES:
- Every decision's "for" becomes a statement Point with IMPL -> the decision event.
- Every "against" becomes a statement Point with NAND -> the decision event.
- Every mitigation becomes a statement Point with MITIGATES -> the support edge it tempers.
- Points argue about their option: about_entities = the option name from the decision's options/chosen or the state item it concerns.
- The logic[] items are points too (they are the durable knowledge; IMPL between them when supports/opposes references exist).
- Keep ids deterministic-looking (pt_<sha>/ev_<sha> — the server re-derives them from content).
- Value filter stays: no filler points."""


def construct_graph(summary: dict, model) -> dict:
    """Step 2: summary -> the epistemic graph structure (the derived stream)."""
    for _ in range(3):
        try:
            resp = _complete(model, CONSTRUCT_SYSTEM,
                             json.dumps(summary, indent=1))
            d = _parse_json(resp)
            if d.get("points") or d.get("events"):
                return d
        except Exception:
            continue
    return {"entities": [], "events": [], "points": [], "operators": []}


# ── The chunked runner + deadline (scale + robustness) ──────────────────────

def _complete(model, system: str, user: str, deadline_s: int = 600) -> str:
    """Wall-clock-bounded completion (no token caps - the model decides)."""
    import threading
    box = {}

    def _run():
        box["resp"] = model.complete(system=system, user=user)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=deadline_s)
    if t.is_alive():
        raise TimeoutError(f"model call exceeded {deadline_s}s")
    return box.get("resp")


def _parse_json(response: str) -> dict:
    m = re.search(r"\{.*\}", response or "", re.S)
    if not m:
        raise ValueError("no JSON block")
    block = m.group(0)
    for cut in (None, -1, -2, -3, -5, -10):
        try:
            return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError:
            continue
    raise ValueError("unparseable JSON")


_REF_RE = re.compile(r"\b(?:PR|docs PR|issue|epic)?\s*#\d{2,6}\b|"
                     r"\bPR\s+#?\d{2,6}\b|"
                     r"\b(?:issue|epic)\s+#?\d{2,6}\b|"
                     r"\bv\d+\.\d+(?:\.\d+)?\b")


def _mask_refs(text: str) -> str:
    return _REF_RE.sub("[REF]", text)


def _user_prompt(edus: list[dict]) -> str:
    return "TRANSCRIPT:\n" + "\n".join(
        f"{e['index']}: {e['role']}: {e['text']}" for e in edus)


def summarize(model, edus: list[dict], chunk_size: int = 6) -> dict:
    """Process 1, chunked for long sessions (per-chunk retry/skip)."""
    def _one(part, system):
        for _ in range(3):
            try:
                return _parse_json(_complete(model, system, _user_prompt(part)))
            except Exception:
                continue
        return None

    if len(edus) <= chunk_size:
        out = _one(edus, SUMMARY_SYSTEM)
        return out or {"session": {"summary": ""}, "state": [], "decisions": [],
                       "logic": [], "issues": []}
    merged = {"session": {"summary": ""}, "state": [], "decisions": [],
              "logic": [], "issues": []}
    for start in range(0, len(edus), chunk_size):
        chunk = edus[start:start + chunk_size]
        prefix = f"ch{start // chunk_size}"
        system = SUMMARY_SYSTEM + (f"\nCHUNK ID PREFIX: prefix item names with "
                                   f"'{prefix}-' where they would collide.")
        part = _one(chunk, system)
        if not part:
            continue
        for k in ("state", "decisions", "logic", "issues"):
            merged[k].extend(part.get(k, []) or [])
    return merged


def ground(summary: dict, existing: dict, model, pre_match: bool = True) -> dict:
    """Process 2: the diff vs the existing graph. pre_match=True runs the
    deterministic name+kind index first (the scale affordance) so the LLM
    only resolves unmatched items."""
    existing_items = existing.get("objects", []) or []
    index = {(str(o.get("name", "")).lower(), o.get("kind", "")): o.get("id")
             for o in existing_items if o.get("name")}
    if pre_match:
        resolved = {}
        for s in summary.get("state", []) or []:
            key = (str(s.get("name", "")).lower(), s.get("objectKind", ""))
            if key in index:
                resolved[s.get("name")] = {"action": "CHANGED",
                                           "supersedes": index[key]}
        if resolved:
            existing = dict(existing)
            existing["pre_matched"] = resolved
    for _ in range(3):
        try:
            resp = _complete(model, GROUNDING_SYSTEM,
                             json.dumps({"summary": _compact(summary),
                                         "existing_state": existing}, indent=1))
            d = _parse_json(resp)
            if d.get("delta"):
                return d
        except Exception:
            continue
    return {"delta": []}


def _compact(summary: dict) -> dict:
    def _trim(items, keep):
        return [{k: v for k, v in (x or {}).items() if k in keep}
                for x in (items or [])]
    return {
        "state": _trim(summary.get("state", []),
                       ("name", "objectKind", "status", "change")),
        "decisions": _trim(summary.get("decisions", []),
                           ("content", "why", "options", "chosen")),
        "logic": _trim(summary.get("logic", []), ("point", "supports", "opposes")),
        "issues": summary.get("issues", []),
    }


def extract_session(model, conversation: list[dict],
                    existing_state: dict | None = None,
                    session_id: str = "session",
                    chunk_size: int = 6) -> dict:
    """The production entry: conversation -> summary (+ delta when existing
    state is provided). Returns the commit-ready stream."""
    edus = [{"index": i, "role": t.get("role", "unknown"),
             "text": _mask_refs(str(t.get("content", "")))}
            for i, t in enumerate(conversation) if t.get("content")]
    summary = summarize(model, edus, chunk_size=chunk_size)
    errors = validate_summary(summary)
    warns = check_guards(summary)
    result = {"session_id": session_id, "summary": summary,
              "errors": errors, "guards": warns}
    if existing_state is not None:
        result["delta"] = ground(summary, existing_state, model)
    return result
