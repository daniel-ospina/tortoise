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
    from tortoise.pack_registry import PackRegistry  # noqa: I001
    from tortoise.pack_registry import default_packs_dir
    import yaml
    packs_dir = Path(packs_dir) if packs_dir else default_packs_dir()
    reg = PackRegistry(packs_dir)
    reg.load_all()
    ns_files = {}
    # NOTE: glob order intentionally NOT sorted — the flag-off S2 prompt and
    # verbose master render must stay byte-identical to main on the same
    # platform, and the pack-manifest glob order (readdir-dependent) is the
    # pre-existing behavior. Deterministic order is guaranteed only
    # downstream: KindIndex.build sorts the kind names, render_s2_prompt
    # sorts the pack-namespace set.
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
    # T12 (#1272): the core objectKind set is aligned to ONTOLOGY §5 Object
    # Kind Vocabulary (16 kinds incl. the commitment-state family) — the
    # prior brief (concept/standard/document/tool/workflow/WorkItem/other)
    # missed project/tag/user/skill/agent/agreement + strategy/plan/goal/
    # target and added concept (not in §5). `concept` is mapped to core:other.
    granularity = {}
    for ns, path in ns_files.items():
        raw = yaml.safe_load(path.read_text()) or {}
        g = (raw.get("ontology") or {}).get("memory_granularity")
        if g:
            granularity[ns] = g

    core = {
        "core:Project": "A project",
        "core:WorkItem": "A unit of work",
        "core:document": "A document artifact",
        "core:tag": "A tag",
        "core:user": "A user",
        "core:skill": "A skill",
        "core:tool": "A tool, CLI, or utility",
        "core:agent": "An agent",
        "core:workflow": "A reusable procedural sequence",
        "core:agreement": "An agreement",
        "core:standard": "A standard, spec, or canonical reference",
        "core:other": "No fitting kind - the explicit uncertain bucket",
        "core:strategy": "A strategy state (commitment-state family)",
        "core:plan": "A plan state (commitment-state family)",
        "core:goal": "A goal state (commitment-state family)",
        "core:target": "A target state (commitment-state family)",
    }
    return {**core, **kinds, "memory_granularity": granularity}


def compile_kind_index_spec(packs_dir: Path | str | None = None) -> dict:
    """The FULL kind-classification candidate set (issue #1695, Task 3):
    ``{kind: {"text", "section", "description", "synonyms", "examples",
    "nearMisses"}}`` for every classifiable kind — core §5 objects, the
    subject kinds, the point kind, the event kinds, and the pack kindDefs
    WITH their full key set (description/synonyms/examples/nearMisses).

    Unlike ``compile_value_brief`` (which drops synonyms/examples — only
    description + nearMisses ride through), this accessor reads
    ``PackManifest.kind_defs`` in full so the kind INDEX can embed a
    re-weighted classification surface (the D0-2 probe refinement path:
    description + synonyms + examples — the plan's mandated re-weight
    before the build commits). The ``text`` field is the surface the index
    embeds; the metadata fields feed the classifier's nearMiss rerank and
    the eval's confusability analysis.

    Candidate/write-gate alignment (review cycle 3): pack pointKinds are
    NOT classifiable (point classification is trivial — "statement" only),
    so point-only kinds are excluded from the spec and the "points"
    section holds ONLY "statement"; and declared-but-kindDefs-less pack
    kinds (eventKinds/objectKinds/documentKinds without a kindDef — e.g.
    ALL 8 pm eventKinds, dev:apiSpec, marketing:keyword) get synthesized
    name-only entries so the classifier can assign them and nearMisses refs
    to them resolve.

    Lazy imports keep the module importable without the pack machinery
    (``extractor_v2`` imports this module's ``compile_value_brief``).

    Memoized per RESOLVED ``packs_dir`` (the key is the resolved path, so a
    custom-dir call never poisons the default-dir memo and vice versa —
    cycle-3 P2 unkeyed memo)."""
    import copy

    from tortoise.pack_registry import default_packs_dir
    global _KIND_SPEC_CACHE
    packs_dir = (Path(packs_dir) if packs_dir else default_packs_dir()).resolve()
    cached = _KIND_SPEC_CACHE.get(str(packs_dir))
    if cached is not None:
        # deep copy — callers must never mutate the shared cache (the
        # build/load paths treat the spec as read-only).
        return copy.deepcopy(cached)
    from tortoise.extractor_v2 import CORE_OBJECT_KEYS, EVENTS, POINTS, SUBJECTS

    def _surface(kind: str, desc: str, syns: list, exs: list) -> str:
        parts = [f"{kind}: {desc}"] if desc else [kind]
        if syns:
            parts.append("synonyms: " + ", ".join(str(s) for s in syns))
        if exs:
            parts.append("examples: " + ", ".join(str(e) for e in exs))
        return " | ".join(parts)

    brief = compile_value_brief(packs_dir)
    spec: dict[str, dict] = {}
    for k in CORE_OBJECT_KEYS:
        desc = str(brief.get(k, "") or "")
        spec[k] = {"text": _surface(k, desc, [], []), "section": "objects",
                   "description": desc, "synonyms": [], "examples": [],
                   "nearMisses": []}
    for k, desc in SUBJECTS.items():
        spec[k] = {"text": _surface(k, str(desc), [], []), "section": "subjects",
                   "description": str(desc), "synonyms": [], "examples": [],
                   "nearMisses": []}
    for k, desc in POINTS.items():
        spec[k] = {"text": _surface(k, str(desc), [], []), "section": "points",
                   "description": str(desc), "synonyms": [], "examples": [],
                   "nearMisses": []}
    for k, desc in EVENTS.items():
        spec[k] = {"text": _surface(k, str(desc), [], []), "section": "events",
                   "description": str(desc), "synonyms": [], "examples": [],
                   "nearMisses": []}
    # packs: the kindDefs FULL key set (compile_value_brief drops
    # synonyms/examples — read the manifests directly); the section is
    # derived from the pack's kind declarations (eventKinds → events,
    # documentKinds/objectKinds → objects) so Task 4's per-type candidate
    # restriction is correct. Pack pointKinds are NOT classifiable — the
    # design doc locks point classification to "statement" (FIX A: the
    # index's "points" section must contain ONLY "statement"), so point-
    # only kinds are SKIPPED from the spec entirely.
    from tortoise.pack_registry import PackRegistry
    reg = PackRegistry(packs_dir)
    reg.load_all()
    for ns, pack in reg.packs.items():
        # Section mapping for DECLARED kinds: eventKinds → events,
        # object/documentKinds → objects (setdefault so a kind declared in
        # BOTH keeps the first non-point section; pointKinds are excluded
        # per FIX A — a point+document kind like marketing:contentBrief
        # lands in objects via its document declaration).
        declared: dict[str, str] = {}
        for k in (pack.event_kinds or []):
            declared.setdefault(k, "events")
        for k in (pack.object_kinds or []) + (pack.document_kinds or []):
            declared.setdefault(k, "objects")
        for kind, kd in (pack.kind_defs or {}).items():
            k = f"{ns}:{kind}"
            # FIX A: a point-only kind (declared in pointKinds, no object/
            # document/event declaration) is never classifiable — skip it.
            if kind in (pack.point_kinds or []) and kind not in declared:
                continue
            desc = str(kd.get("description", brief.get(k, "")) or "")
            syns = [str(s) for s in (kd.get("synonyms") or [])]
            exs = [str(e) for e in (kd.get("examples") or [])]
            nms = [str(n) for n in (kd.get("nearMisses") or [])]
            spec[k] = {"text": _surface(k, desc, syns, exs),
                       "section": declared.get(kind, "objects"),
                       "description": desc,
                       "synonyms": syns, "examples": exs,
                       "nearMisses": nms}
        # FIX L: declared-but-kindDefs-less pack kinds (dev:apiSpec,
        # marketing:keyword, pm:milestone, ALL 8 pm eventKinds, ...) are
        # never in the index → the classifier can't assign them and
        # nearMisses refs to them resolve to ∅. Synthesize name-only spec
        # entries (section derived from the declaration; pointKinds already
        # excluded above per FIX A). An existing kindDefs entry is never
        # clobbered.
        for kind, section in declared.items():
            k = f"{ns}:{kind}"
            if k in spec:
                continue  # a kindDefs entry already rode through
            spec[k] = {"text": k, "section": section,
                       "description": "", "synonyms": [], "examples": [],
                       "nearMisses": []}
    _KIND_SPEC_CACHE[str(packs_dir)] = spec
    return copy.deepcopy(spec)


_VOCAB_CACHE: dict | None = None


#: Load-once memo for the kind-index spec, keyed by the RESOLVED packs dir
#: (packs are static per process per dir — per-session classifier
#: construction must not re-read + YAML-parse every pack manifest; the key
#: keeps a custom-dir call from poisoning the default-dir memo and vice
#: versa — cycle-3 P2 unkeyed memo). Mirrors ``_MASTER_LIST_CACHE`` /
#: ``_VOCAB_CACHE``.
_KIND_SPEC_CACHE: dict[str, dict] = {}


def _clear_kind_spec_cache() -> None:
    """Test hook — clear ALL memoized kind-index specs (cross-test
    isolation; the per-session re-parse is exactly what the memo avoids)."""
    global _KIND_SPEC_CACHE
    _KIND_SPEC_CACHE = {}


def _object_kind_vocab() -> set[str]:
    """The closed objectKind set (core §5 + pack kinds), cached (T12 —
    compile_value_brief does PackRegistry.load_all() per call). Bare forms
    are normalized to their namespaced key; case is folded."""
    global _VOCAB_CACHE
    if _VOCAB_CACHE is None:
        brief = compile_value_brief()
        vocab = set()
        for key in brief:
            ns, _, kind = key.rpartition(":")
            vocab.add(key)
            vocab.add(kind)                       # bare form
            vocab.add(kind.lower())              # case-folded
            vocab.add(f"{ns}:{kind.lower()}")   # namespaced + folded
        _VOCAB_CACHE = vocab
    return _VOCAB_CACHE


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
STATE = what IS after this session (durable epistemic objects per the
ontology, incl. OPTIONS retained AND discarded). STRICT EXCLUSION — do NOT
emit as state: file paths, module names, branch names, worktrees, test
suites/files, issue ids/labels, git operations, commands run, or any work
artifact touched. State is what the session CHANGED ABOUT THE WORLD that
remains true and durable (an option chosen, an approach adopted, a ruling, a
feature decision) — NOT the mechanics of how the work was done. If it is a
file/branch/test the agent worked on, it is execution narration, drop it.
Aim for 2-8 state items, not one per artifact. DECISIONS = commitments resolving the confidence
dynamics, with the WHY + FOR/AGAINST + MITIGATIONS, in posterity wording.
ISSUES = created/completed referenced (precise lifecycle syncs from GitHub).
LOGIC = the durable beliefs/knowledge with their sources (edu_refs as
supporting evidence). Empty sections are fine - extract-nothing is valid.

DECISION GATE (R1∧R3 — the real discriminator): a DECISION is a COMMISSIVE
(commits to a future action: decided / chose / agreed to / we're going with /
the ruling is / default to / ship X first / reject Y) AND product-knowledge-
bearing (asserts something durable about the domain: a choice of approach, a
ruling, a commitment with epistemic weight). Exclusions:
- "should" is a RECOMMENDATION, never a decision (measured 44× false positive).
- "will" is ambiguous (prediction vs commitment) — discriminate on subject
  agentivity + product-knowledge-bearing.
- Process/work commitments ("I'll fix both now", "let me verify X", "I'll
  commit after review") are NOT decisions — they are execution narration;
  drop them (they are the S0 noise the value filter removes).
- Commit/push/PR lifecycle events are NOT decisions — they are Events better
  derived from GitHub ingestion; do not emit them as decisions.
If a statement is durable domain knowledge ("X implies Y", "Z costs W", "the
cause is Q"), put it in LOGIC, not DECISIONS.
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


def validate_summary(summary: dict, vocab: dict | None = None,
                    mode: str = "fail-closed") -> list[str]:
    """Deterministic validation of the summary stream (the enforcer).

    T12 (#1272): ``mode`` selects fail-closed (default — non-vocab
    objectKinds reject) vs warn (Phase B calibration — non-vocab kinds
    become proposal notes, not errors, so the calibration windows are
    reachable; criteria v1 §2.2.6 proposal semantics). The kind check is
    ALWAYS active against the aligned §5+pack vocab (never gated on vocab
    being passed — the vocab is loaded internally when None)."""
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
        kind = s.get("objectKind")
        if not kind:
            errors.append(f"state {s.get('name','')[:30]}: missing objectKind "
                          "(fail-closed — the mapper default core:other is a "
                          "payload fallback, not a validation pass)")
        elif kind not in _object_kind_vocab():
            if mode == "fail-closed":
                errors.append(f"state {s.get('name','')[:30]}: objectKind "
                              f"{kind!r} not in the closed vocabulary "
                              "(minted kind — T12)")
            else:
                # warn mode: proposal note, not an error (Phase B).
                pass
    for l in summary.get("logic", []) or []:  # noqa: E741
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
    {"src": "pt-id", "dst": "ev-id", "op_type": "MITIGATES",
     "target": {"src": "pt-id", "dst": "ev-id", "op_type": "IMPL"},
     "strength": 0.3}   # a mitigation tempers a support edge (target = the edge-identity triple)
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


CORRECT_PASS = """You are the CORRECTION pass. The session summary below failed
validation with these errors:
{errors}

The items' names/ids are FIXED. Produce the FULL corrected summary (same keys:
session/state/decisions/logic/issues) fixing ONLY the listed errors (add the
missing sources/why, trim over-long content). Do not invent new items. One
JSON object."""


def summarize(model, edus: list[dict], chunk_size: int = 6) -> dict:
    """Process 1, chunked for long sessions (per-chunk retry/skip).

    T7/T8/T9 (#1272): the merge aggregates per-chunk ``session`` blocks
    (summary concatenated, capped at 2000; type preserved), drops the
    ``ch{n}-`` name prefix (it poisoned the (name, kind) MERGE key),
    de-duplicates state items by (name, kind) across chunks, and counts
    failed chunks so partial extraction is never silent.
    """
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
                       "logic": [], "issues": [], "failed_chunks": 0}
    merged = {"session": {"summary": ""}, "state": [], "decisions": [],
              "logic": [], "issues": [], "failed_chunks": 0}
    summaries: list[str] = []
    session_type: str | None = None
    for start in range(0, len(edus), chunk_size):
        chunk = edus[start:start + chunk_size]
        part = _one(chunk, SUMMARY_SYSTEM)  # T8: no ch{n}- prefix instruction
        if not part:
            merged["failed_chunks"] += 1
            continue
        sess = part.get("session") or {}
        if sess.get("summary"):
            summaries.append(str(sess["summary"]))
        if sess.get("type") and session_type is None:
            session_type = str(sess["type"])
        for k in ("state", "decisions", "logic", "issues"):
            merged[k].extend(part.get(k, []) or [])
    # T8: cross-chunk (name, kind) dedup — run ONCE on the accumulated list.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for st in merged["state"]:
        key = (st.get("name", ""), st.get("objectKind", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(st)
    merged["state"] = deduped
    merged["session"] = {
        "summary": " ".join(summaries)[:2000],   # T7: concatenate, cap at 2000
        "type": session_type or "",
    }
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
                    chunk_size: int = 6,
                    mode: str = "fail-closed") -> dict:
    """The production entry: conversation -> summary (+ delta when existing
    state is provided). Returns the commit-ready stream.

    T10 (#1272): a bounded R4 repair loop — if validate_summary flags errors,
    the model is re-prompted ONCE with the errors (CORRECT_PASS) to fix them
    (≤1 retry; the harness's proven run_iterative pattern, production-schema
    adapted). The mode param (T12) selects fail-closed (default) vs warn.
    """
    edus = [{"index": i, "role": t.get("role", "unknown"),
             "text": _mask_refs(str(t.get("content", "")))}
            for i, t in enumerate(conversation) if t.get("content")]
    summary = summarize(model, edus, chunk_size=chunk_size)
    errors = validate_summary(summary, mode=mode)
    if errors:
        # T10: one bounded repair attempt (the dev lineage's CORRECT_PASS).
        try:
            fixed = _parse_json(_complete(
                model, CORRECT_PASS.format(errors="\n".join(errors[:8])),
                json.dumps(summary, indent=1)))
            if isinstance(fixed, dict):
                fixed_errors = validate_summary(fixed)
                if len(fixed_errors) < len(errors):
                    summary = fixed
                    errors = fixed_errors
        except Exception:
            pass  # repair is best-effort; original errors stand
    warns = check_guards(summary)
    result = {"session_id": session_id, "summary": summary,
              "errors": errors, "guards": warns}
    if existing_state is not None:
        result["delta"] = ground(summary, existing_state, model)
    return result
