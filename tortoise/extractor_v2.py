"""Extractor pipeline v2 — the 5-stage narrative-first architecture
(epic #909, issue #1350).

Design contract: docs/plans/2026-08-14-extractor-pipeline-v2-design.md

```
raw conversation
  │
  ▼
[S1] STORY SUMMARY (chunked)  — flash prompt, validated + owner-approved
     (run_s1_granular.py lineage); per-chunk narratives COMPILED into one
     coherent story (dedup entities, stitch the arc).
  │
  ▼
[S2] MAP TO EMBED — flash prompt: story + master list + condensed
     how-to-use-tortoise semantic core → embed list
     {entities, events, points, operators, chain_notes, link_before_create}.
  │
  ▼
[S3] SEARCH THE GRAPH — REAL backend read (FalkorDB via docker:// / redis://
     URI, or hosted API), NOT FalkorDBLite. Degrades gracefully when the
     backend is embedded or unreachable (the pipeline proceeds empty).
  │
  ▼
[S4] REVIEW GAPS — flash prompt: compiled story + S3 results + S2 list →
     COMPLETE embed list.
  │
  ▼
[S5] EMBED — EXECUTION (deterministic, not a flash prompt): resolve the
     complete embed list against the existing-graph search results
     (link-before-create), supersede (REVISES), validate chains (warn +
     repair where the data allows), emit the Layer-1 commit payload in
     dependency order (entities → events → points → operators).
```

Owner confirmations honored (issue #1350): flash runs 4 prompts (S1-S4), S5
is execution; temperature 0.0 (via tests.model_adapters MODELS); S3 reads the
REAL backend; chains warn + repair, hard-block never; solar-pro4 parked;
narrative-first; master list = objects + subjects + points + events + packs +
chains; single-flash path; no max_tokens caps.

Master-list note: the v2 master list (subjects/points/events/chains) lives
here as ``build_master_list()`` — an overlay over the v1
``compile_value_brief()`` (which stays the kinds+granularity contract for the
v1 enforcer; adding non-kind sections to it would pollute
``value_extractor._object_kind_vocab``). The expansion is delivered by this
module per design doc §3.
"""
from __future__ import annotations

import contextlib
import inspect
import json
import os
import random
import re
import time
import warnings
import weakref
from typing import Any

# ── The v2 master list (design doc §3) ─────────────────────────────────────

SUBJECTS = {
    "core:organization": "An organization — a company, agency, or other collective entity",
    "core:team": "A team — a group of people working together toward shared goals",
    "core:role": "A role — a defined function or position held by a person or a team",
    "core:legalPerson": "A legal person — an entity with legal standing (company, foundation, org)",
    "core:naturalPerson": "A natural person — an individual human being",
}

POINTS = {
    "statement": "A durable belief, claim, or proposition — the extraction write kind",
}

EVENTS = {
    "core:decision": "A commitment event — a choice made with reasons that resolves confidence",
    "core:occurrence": "An occurrence — something that happened at a point in time",
    "core:deployment": "A deployment event — a product or release shipped to an environment",
    "core:review": "A review event — a review of work, code, plan, or content",
    "core:meeting": "A meeting event — a gathering that changed or confirmed state",
    "core:experiment": "An experiment event — a test or calibration run with measured results",
    "core:friction": "A friction event — a discovered obstacle, pain point, or workflow failure",
}

# Chain paths use the CANONICAL kind names from the master list (no minting):
# "jobToBeDone" is the design doc's JTBD (product-strategy:jobToBeDone).
CHAINS = {
    "productDelivery": {
        "path": ["jobToBeDone", "useCase", "feature", "userJourney",
                 "workflow", "requirement", "architecture"],
        "note": ("Customer value flows toward delivery: customers connect to "
                 "JTBDs/use cases, not directly to architecture. A customer "
                 "mapped straight to an architecture requirement re-maps to "
                 "the nearest chain position."),
    },
    "epicToCode": {
        "path": ["epic", "issue", "code"],
        "note": "Work decomposition: an epic breaks into issues; issues land in code.",
    },
    "campaignToChannel": {
        "path": ["campaign", "content", "channel"],
        "note": "Marketing flow: a campaign produces content that reaches an audience through a channel.",
    },
}

PACK_NS = ("product-strategy:", "dev:", "marketing:", "pm:")

# E2 (#1534): the USER-PERSONAL-STATE vocabulary — the operative criterion for
# the Tier-A classification hint (personal bests, schedules, preferences). The
# VALUE is the fact; retain it verbatim. This is prompt-guidance ONLY: it is a
# rendered section of the master list, NEVER part of the closed-kind set
# (master_kind_forms iterates a fixed section tuple) and NEVER a new kind.
USER_PERSONAL_STATE = {
    "personal_best": (
        "A personal record/achievement VALUE — times, distances, scores, "
        "quantities ('my personal best 5K time is 27:12'). The VALUE is the "
        "fact; retain it verbatim."
    ),
    "schedule": (
        "A recurring commitment VALUE — regular times, days, frequencies "
        "('gym at 6pm', 'standup at 9:30'). The TIME is the fact; retain it verbatim."
    ),
    "preference": (
        "A stated preference/choice VALUE — likes, dislikes, defaults, chosen "
        "options ('prefers dark mode', 'coffee not tea'). The CHOICE is the "
        "fact; retain it verbatim."
    ),
}

# E2 (D3): the value-filter carve-out — distinguishes MECHANICS TOKENS (drop:
# ids, hashes, counts as process metrics) from STATE VALUES (keep verbatim:
# personal bests, schedules, preferences — the value IS the fact). Shared by
# S1 (_granularity_text), S2 and S4 (via _render_master).
STATE_VALUE_CARVE_OUT = (
    "STATE-VALUE CARVE-OUT (applies to every domain above): user-personal-state "
    "VALUES — personal bests, schedules, preferences — are DURABLE even where "
    "counts/logistics are ephemeral. Carry the VALUE verbatim ('my personal "
    "best 5K time is 27:12', not 'the user has a fast 5K'). The value is the "
    "fact; it is NOT a mechanics token."
)

CORE_OBJECT_KEYS = (
    "core:Project", "core:WorkItem", "core:document", "core:tag",
    "core:user", "core:skill", "core:tool", "core:agent",
    "core:workflow", "core:agreement", "core:standard", "core:other",
    "core:strategy", "core:plan", "core:goal", "core:target",
)


def _desc(brief: dict, key: str) -> str:
    v = brief.get(key, "")
    return v.get("description", "") if isinstance(v, dict) else str(v or "")


_MASTER_LIST_CACHE: dict | None = None


def build_master_list() -> dict:
    """The v2 master list: compile_value_brief() kinds + the §3 additions
    (subjects, points, events, chains, memory_granularity). Section values
    are {kind: description} dicts — rendered as readable text in prompts.

    Memoized (#1350 chunking finding): the packs are static per process —
    re-reading + YAML-parsing every manifest per chunk cost 12.2s of a 14.3s
    60-chunk run. Returns a deep copy so callers can't mutate the cache.
    """
    import copy
    global _MASTER_LIST_CACHE
    if _MASTER_LIST_CACHE is not None:
        return copy.deepcopy(_MASTER_LIST_CACHE)
    from tortoise.value_extractor import compile_value_brief
    brief = compile_value_brief()
    objects = {k: _desc(brief, k) for k in CORE_OBJECT_KEYS}
    pack_kinds = {}
    for k, v in brief.items():  # noqa: B007
        if k == "memory_granularity":
            continue
        if not k.startswith(PACK_NS):
            continue
        pack_kinds[k] = _desc(brief, k)
    master = {
        "objects": objects,
        "subjects": dict(SUBJECTS),
        "points": dict(POINTS),
        "events": dict(EVENTS),
        "pack_kinds": pack_kinds,
        "chains": {name: {"path": list(c["path"]), "note": c["note"]}
                   for name, c in CHAINS.items()},
        "memory_granularity": dict(brief.get("memory_granularity", {})),
        # E2 (#1534): user-personal-state vocabulary — the Tier-A classification
        # hint criterion. NOT kinds (master_kind_forms' section tuple excludes
        # it); rendered into S2/S4 prompt context only.
        "user_personal_state": dict(USER_PERSONAL_STATE),
    }
    _MASTER_LIST_CACHE = copy.deepcopy(master)
    return master


def master_kind_forms(master: dict) -> set[str]:
    """Namespaced + bare + case-folded forms of every kind in the master list
    (the closed-vocab check set — S5's minted-kind gate)."""
    kinds: set[str] = set()
    for section in ("objects", "subjects", "points", "events", "pack_kinds"):
        for key in master.get(section, {}):
            ns, _, kind = key.rpartition(":")
            kinds.add(key)
            if kind:
                kinds.add(kind)
                kinds.add(kind.lower())
                if ns:
                    kinds.add(f"{ns}:{kind.lower()}")
            else:
                kinds.add(key.lower())
    return kinds


def _render_master(master: dict) -> str:
    lines = [
        "MASTER LIST — the closed vocabulary. EVERY kind you emit MUST come "
        "from this list (namespaced or bare form). Do NOT mint kinds: "
        "\"worktree\", \"test suite\", \"approach\" are NOT kinds — re-map "
        "to the nearest listed kind or drop the item.",
    ]

    def _group(title: str, d: dict) -> str:
        out = [f"\n{title}"]
        out += [f"- {k} — {v}" for k, v in d.items()]
        return "\n".join(out)

    lines.append(_group("OBJECTS (core)", master["objects"]))
    lines.append(_group("SUBJECTS (core)", master["subjects"]))
    lines.append(_group("POINTS", master["points"]))
    lines.append(_group("EVENTS", master["events"]))
    lines.append(_group("PACK KINDS (from the installed packs)", master["pack_kinds"]))

    lines.append("\nCHAINS (the business logic of mapping)")
    for name, c in master["chains"].items():
        lines.append(f"- {name}: {' → '.join(c['path'])}")
        lines.append(f"    {c['note']}")

    g = master["memory_granularity"]
    if g:
        lines.append("\nMEMORY GRANULARITY (what each domain considers DURABLE "
                     "vs EPHEMERAL — the retention bar)")
        lines += [f"- {ns}: {txt}" for ns, txt in g.items()]

    # E2 (#1534): the user-personal-state vocabulary — the operative criterion
    # for the Tier-A classification hint. Explicit hint-not-kind guard: the
    # vocabulary is classification guidance, never entity/event/point kinds.
    lines.append("\nUSER-PERSONAL-STATE VOCABULARY (Tier-A classification "
                 "hint — these are NOT kinds: do NOT emit them as "
                 "entity/event/point kinds. A fact matching one is Tier-A → a "
                 "statement Point with tier:\"A\", the verbatim value in "
                 "content, and the verbatim source in quote):")
    lines += [f"- {cat} — {desc}"
              for cat, desc in master["user_personal_state"].items()]
    if g:
        lines.append("")
        lines.append(STATE_VALUE_CARVE_OUT)
    return "\n".join(lines)


def _render_chains(master: dict) -> str:
    return "\n".join(
        f"- {name}: {' → '.join(c['path'])} — {c['note']}"
        for name, c in master["chains"].items()
    )


# ── S1: STORY SUMMARY (chunked) — the validated prompt ─────────────────────

S1_TMPL = """You are the STORY SUMMARIZER for the company/product epistemic memory.
Read the whole conversation. Produce a NARRATIVE that captures what CHANGED
about the world we operate in — the state of the product, the team, the
domain — and WHY it changed, at the level of durable meaning, not mechanics.

Use the MEMORY GRANULARITY definitions below as the rule for what to keep
(durable) vs drop (ephemeral) — not a vague time heuristic:

{memory_granularity}

Apply these per domain. When a fact spans domains, keep it if ANY domain
considers it durable. When unsure: "is this a decision, a state change, a
durable belief, or a reason — or is it how the work was done this hour?"

{date_anchor}Focus on TWO primary layers, in this order:
1. STATE (primary): subjects and objects and how they changed.
2. EPISTEMIC (primary): the LOGIC — points that support (IMPL), attack (NAND),
   or mitigate the relevance (MITIGATES) between points and objects.
EVENTS (secondary): only as context for why state changed.
OPERATIONAL KNOWLEDGE (tertiary but DURABLE — do not drop it): cause-effect
lessons about how the environment behaves and how the team works, when they
would change future behavior: tool/process behaviors ("the bash tool kills
child processes when it returns", "setsid does not exist on macOS",
"pytest-timeout is not installed"), workflow rules ("issues without fractal
fields default to task-workflow-standard"), and conventions that future
sessions must know. These are durable beliefs, not process chatter — keep
the CAUSE-EFFECT, drop the episode.

De-emphasize process — no commit hashes, no test counts, no PR numbers, no
review findings, no tool calls, no build steps — unless they DIRECTLY change
state or reveal durable belief.

STRIP, DON'T DROP: a decision that references an issue/PR is still a
DECISION — strip the id, keep the decision ("migrate EP tests to live points
rather than change production semantics", not "for #992, migrate..."). An
operational lesson is still a BELIEF — strip the episode, keep the
cause-effect. The mechanics tokens (ids, hashes, counts, paths) are noise;
the claim they carry is not.

RESTATE, DON'T REINVENT: if the conversation states a root cause or a fact,
preserve it exactly. Do NOT invent a "We believed X" opening unless the input
supports it.

The narrative should read like: "We believed X. The session revealed Y, which
changed our approach to Z. The reasoning: A supports it, B undermines it, C
tempers how much it matters."

Granularity: the level of a decision (its resulting change in state, the
tradeoffs and reasons behind) worth remembering in six months, per the
memory-granularity rules above. If a detail won't matter then, drop it."""


def _granularity_text() -> str:
    """The S1 memory-granularity slot. E2 (D3): appends the STATE-VALUE
    CARVE-OUT so S1's granularity rules protect user-personal-state values
    from the mechanics-token filter — S1's "RESTATE, DON'T REINVENT" rule
    then carries the value verbatim into the story."""
    master = build_master_list()
    g = master.get("memory_granularity", {})
    out = "\n".join(f"- {ns}: {txt}" for ns, txt in g.items())
    return f"{out}\n{STATE_VALUE_CARVE_OUT}" if out else STATE_VALUE_CARVE_OUT


# ── Session-date anchoring (E1, #1533) ────────────────────────────────────

_DATE_ANCHOR_D2 = (
    "**DATE ANCHOR — today is `{session_date}`.** Anchor every state change, "
    "decision, and event to this date. Express time with ABSOLUTE ISO dates "
    "(YYYY-MM-DD); resolve relative expressions (\"yesterday\", \"last week\", "
    "\"recently\") against today. Never leave relative time in the narrative."
)

_DATE_ANCHOR_D3 = (
    "EVENT `startedAt` — every event is a time-bound occurrence/decision: "
    "use the conversation's stated date when present, else default to the "
    "session date; `null` only when the session date is unknown.\n"
    "POINT `when` — emit an ISO date when the point is a state-change, "
    "decision, or date-bearing fact (\"as of {date}\", \"on {date}\", "
    "\"since {date}\"); `null` for timeless durable beliefs (operational "
    "lessons, stable facts) — do NOT stamp every point."
)


def _date_anchor(session_date: str | None,
                 *, include_emission_rules: bool = False) -> str:
    """The bounded {date_anchor} prompt block (E1, #1533 — mem0 write-time
    pattern). Returns "" when the session is undated so the placeholder
    renders to ZERO bytes — undated S1 prompts are byte-identical to pre-E1
    (S2/S4 still differ from pre-E1 only via the unconditional OUTPUT_CONTRACT
    ``when``/``startedAt`` fields). The trailing blank line is part of the
    block (templates place {date_anchor} flush against the following
    paragraph), so a dated render is cleanly separated from what follows.
    S1 renders the D2 anchor paragraph only, while S2/S4 also render the D3
    emission rules for `when`/`startedAt`."""
    if not session_date:
        return ""
    block = [_DATE_ANCHOR_D2.format(session_date=session_date)]
    if include_emission_rules:
        block.append(_DATE_ANCHOR_D3.format(date=session_date))
    return "\n\n".join(block) + "\n\n"


def _valid_iso_date(v: str) -> bool:
    """Deterministic date-acceptance gate (E1, #1533 — S5 normalization):
    accepts the YYYY-MM-DD prefix with an optional T/space tail (a bare date
    or a full ISO datetime), bounded to 40 chars (the commit_schema
    ``Point.when`` max_length — an over-long value would otherwise pass the
    gate and sink the WHOLE payload at Layer-1). Anything else ("next
    tuesday", "null", "") is junk — the caller drops it with a warning."""
    s = str(v or "").strip()
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}([Tt ].*)?$", s) and len(s) <= 40)


def run_s1(model, transcript: str, *,
           session_date: str | None = None,
           stats: dict | None = None) -> str:
    """S1: story summary for ONE segment. Returns the narrative text
    (the validated single-flash path). Generation is bounded at
    ``_S1_MAX_TOKENS`` (M3 #1524, D2 — capped output, truncation detected
    via ``last_finish_reason == "length"``, never silently lost)."""
    system = (S1_TMPL
              .replace("{memory_granularity}", _granularity_text())
              .replace("{date_anchor}", _date_anchor(session_date)))
    return _complete(model, system, "CONVERSATION:\n" + transcript,
                     max_tokens=_stage_cap(_S1_MAX_TOKENS), stats=stats)


# ── The chunker + compiler (design doc §2) ─────────────────────────────────

def chunk_transcript(edus: list[dict], target: int = 50) -> list[list[dict]]:
    """Split the EDU stream into bounded contiguous segments (design doc:
    40-60 EDUs per chunk). Each segment runs S1 independently, then the
    compiler stitches them."""
    if target < 1:
        raise ValueError("target chunk size must be >= 1")
    return [edus[i:i + target] for i in range(0, len(edus), target)]


def _edus_to_text(edus: list[dict]) -> str:
    return "\n".join(
        f"{e['index']}: {e['role']}: {e['text']}" for e in edus)


def _norm_sent(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _overlap_ratio(a: str, b: str) -> float:
    """Token-overlap ratio used by the compiler's cross-chunk dedup.

    Requires near-symmetric length (max/min < 1.5) so a shorter NEW sentence
    that is a token-subset of an earlier one is NOT dropped as a duplicate
    (asymmetric-ratio false dedup)."""
    ta = set(_norm_sent(a).split())
    tb = set(_norm_sent(b).split())
    if not ta or not tb:
        return 0.0
    lo, hi = min(len(ta), len(tb)), max(len(ta), len(tb))
    if hi / lo >= 1.5:
        return 0.0
    return len(ta & tb) / lo


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def compile_stories(stories: list[str]) -> str:
    """Compile per-chunk S1 narratives into ONE coherent story.

    - Stitch the arc: chunks keep their order, joined with paragraph breaks.
    - Cross-chunk dedup: a sentence in chunk N that is a near-duplicate
      (token overlap >= 0.85) of something already merged is dropped — the
      entity/claim is preserved once, not re-introduced as new.
    - Cross-chunk connections survive because the dedup keeps the FIRST
      (earliest) articulation and later chunks only re-reference it.
    Returns the compiled story text ('' when no chunks)."""
    if not stories:
        return ""
    merged: list[str] = []
    seen_sents: list[str] = []
    for story in stories:
        for sent in _split_sentences(story):
            if len(set(_norm_sent(sent).split())) < 3:
                # fragments (<3 tokens) are not dedup candidates — pass through
                merged.append(sent)
                continue
            dup = any(_overlap_ratio(sent, prev) >= 0.85
                      for prev in seen_sents)
            if not dup:
                merged.append(sent)
                seen_sents.append(sent)
    return "\n\n".join(merged)


# ── S2: MAP TO EMBED — the draft prompt (owner-in-the-loop v1) ─────────────

OUTPUT_CONTRACT = """{
  "entities": [{"name": str, "kind": str, "lifecycle": "created|changed|unchanged|superseded", "supersedes": "existing-id|null", "note": str|null}],
  "events": [{"content": str, "eventKind": str, "about_entities": [str], "startedAt": "YYYY-MM-DD|YYYY-MM-DDThh:mm:ss|null", "slots": {"subject": [{"name": str, "kind": str, "confidence": float}], "object": [{"name": str, "kind": str, "confidence": float}], "event": [{"name": str, "kind": str, "confidence": float}]}}],
  "points": [{"content": str, "pointKind": "statement", "about_entities": [str], "when": "YYYY-MM-DD|null",
              "slots": {"subject": [...], "object": [...], "event": [...]},
              "tier": "A|B",            # Tier-A state-value marker (E2); omit = Tier-B
              "quote": str|null,          # verbatim source text, <=200 chars (E3)
              "search_keys": [str, ...],  # 2-4 aliases + verbatim value tokens (E3)
              "source_turn_id": int|null}],  # {index}: turn in the SOURCE TRANSCRIPT (E3)
  "operators": [
    {"src": str, "dst": str, "op_type": "IMPL|NAND"},
    {"src": str, "dst": str, "op_type": "MITIGATES", "target_edge": {"src": str, "dst": str, "op_type": "IMPL"}, "strength": 0.1|0.3|0.5}
  ],
  "chain_notes": [{"chain": str, "finding": str, "action": "repaired|warned", "note": str}],
  "link_before_create": [{"searched_for": str, "found": bool, "note": str}]
}"""


# E3 (issue #1535): the SOURCE TRANSCRIPT block cap (chars) — protects the
# S2/S4 input token budget (D3). Over cap the block is omitted; the
# deterministic quote→turn resolver still anchors from `quote` alone.
_SOURCE_TRANSCRIPT_CAP = 8000

S2_TMPL = """You are the GRAPH MAPPER for the Tortoise epistemic memory.

TASK
Map the S1 STORY into the exact graph-write embed list: objects/subjects →
entities (with lifecycle), events → Event nodes, points → Points (statement),
connections → IMPL/NAND/MITIGATES operators. Reference ENTITIES by their names,
EVENTS by their content, POINTS by their content.

MASTER LIST
{master_list}

CONDENSED SEMANTIC CORE (from the how-to-use-tortoise skill)
- Edge types: IMPL = supports/implies; NAND = contradicts.
- TRUTH vs WEIGHT — two different tools for two different problems:
  * A claim that is FACTUALLY WRONG → NAND the Point directly (truth attack).
    Truth lives on the POINT.
  * A claim that is TRUE but matters LESS than it seems → MITIGATES on the
    OPERATOR/edge (relevance attack): strength 0.10 (minor caveat) / 0.30
    (significant limitation) / 0.50 (major counter-evidence). NEVER use < 0.10
    (negligible) or > 0.50 (would invert the claim — use NAND instead).
  * Golden rule: relevance lives on the OPERATOR, truth lives on the POINT.
  * NEVER NAND an option or criterion for being a bad fit — options and
    criteria are true by definition. A bad fit is a relevance problem: attack
    the OPERATOR (NAND or MITIGATES on the edge between them).
- LIFECYCLE (supersession):
  * created     = genuinely new item (searched the graph first, no match).
  * changed     = updates an existing item — carry "supersedes": "existing-id"
    when you know the id, else null.
  * unchanged   = already exists — connect only, do NOT re-create.
  * superseded  = this item is being replaced by a newer one.
- SUPERSESSION (state objects/subjects — the state-centric lifecycle): when
  the conversation shows an entity being REPLACED — adopted B over A, "drop
  the old approach for X", "the new strategy supersedes the old one" — emit
  the NEW entity (lifecycle created) with "supersedes" set to the superseded
  item's NAME when identifiable ("strategy-A"), else null. The existing
  graph id is filled later by the gap-review/search stage — never invent an
  id here. The OLD entity is NOT re-created — supersede, don't duplicate.
  Anaphora resolves to the existing graph item: "the old strategy" / "the
  previous approach" refers to the existing entity of that kind. Emit ONE
  statement point capturing the replacement ("strategy-B supersedes
  strategy-A") with about_entities listing BOTH the new and the superseded
  entity — that point is the graph-visible record.
- DECISION EVENTS — ONLY when real: emit a decision event ONLY when the
  conversation contains a genuine commitment with reasons ("we decided",
  "the ruling is", "we're going with X"). NEVER fabricate a decision event
  for a supersession, a completion, or a process step — those are entity
  lifecycle + occurrence events, not decisions.
- RECOUP DONE-THINGS as occurrence events: when the text shows something was
  completed (a PR merged, an issue closed, a release shipped, an approach
  finished), emit an occurrence event capturing the DONE state stripped of
  mechanics ids ("the EP test migration shipped", not "PR #1004 merged"). The
  session's event stream includes what got done, not just decisions.
- ENTITY NAMES CARRY NO MECHANICS: entity names are durable subjects/objects —
  strip ids and numbers ("EP test migration", not "pr #1004: ep test migration";
  "the draft-filter fix", not "issue #992"). Issue/PR/commit identifiers never
  appear in entity names, event content, or point content.
- PARTICIPANT SLOTS (typed roles, #1418): for EVERY point AND event emit
  "slots" naming its participants BY ROLE: subject = the acting/held entity
  the claim is about; object = the affected/targeted entity; event = an
  EVENT this content is ABOUT (event-as-content). Each slot entry is
  {"name", "kind", "confidence"} — name/kind from the MASTER LIST vocabulary
  (no minted kinds; subject kinds from SUBJECTS, object kinds from OBJECTS,
  event kinds from EVENTS), confidence ∈ [0,1] = your certainty the role is
  right. Omit empty roles (no key or []).
  subject/object names MUST reference an entity you emit in the entities[]
  of THIS SAME output (unresolvable refs are dropped at execution — never
  slot an entity you did not emit).
  THE EVENT SLOT IS CONTENT, NEVER PROVENANCE (#1417): it names an event the
  claim discusses ("the Aug 3 meeting"), never how this session captured it.
  "The Aug 3 meeting concluded X" → event: [{"name": "the Aug 3 meeting",
  "kind": "core:meeting", "confidence": 0.8}]. Do NOT slot capture/
  process events.
- LINK-BEFORE-CREATE: before creating ANY entity or point, search the graph for
  an existing item (same name/topic). Record each search in link_before_create
  (searched_for, found, note). No duplicates — dedup.
- CHAINS — mapping must respect the chain positions (WARN, then TRY TO REPAIR):
{chains_text}
  If a mapping would connect across a chain in a way that violates it, WARN in
  chain_notes and TRY TO REPAIR by re-mapping toward the nearest valid chain
  position. NEVER invent entities to satisfy a chain.

OPERATOR REFERENCING (hard rule)
Operators wire POINTS to POINTS or POINTS to EVENTS. The src/dst strings MUST
be the EXACT content of a point or event emitted in THIS same output — copy
verbatim, no paraphrasing, no prefixes, no truncation. If an endpoint of an
IMPL/NAND/MITIGATES relation has no point yet, CREATE the point first and
reference it. NEVER use an entity name as an operator endpoint — entities are
wired through about_entities, not through operators.

VALUE FILTER — STRICT EXCLUSION (carried from v1), SHARPENED
Mechanics TOKENS drop: file paths, module names, branch names, worktrees,
test suites/files, issue ids/labels, git operations, commands run, commit
hashes, PR numbers, test counts, review findings, tool calls, build steps.
But STRIP, DON'T DROP the durable claim they carry:
- A decision that references an issue/PR is still a DECISION — strip the id,
  keep the decision ("migrate EP tests to live points rather than change
  production semantics", not "for #992, migrate...").
- An operational/environment lesson is a durable BELIEF — keep the
  cause-effect ("the bash tool kills child processes when it returns",
  "backgrounded processes do not survive tool-call return"), drop the episode.
- Process chatter with NO durable claim is dropped ("I'll fix both now",
  "let me verify X").
What survives is what changes the world model — including how we work.

CARVE-OUT — USER-PERSONAL-STATE VALUES ARE NOT MECHANICS TOKENS
The exclusion list above targets PROCESS ARTIFACTS (issue ids, commit hashes,
PR numbers, test counts as process metrics, file paths, commands, tool calls).
A user-personal-state VALUE — a personal-best time ("27:12"), a schedule slot
("gym at 6pm"), a preference ("coffee not tea") — is the FACT; the value is
not a mechanics token — keep it VERBATIM: never strip, round, paraphrase, or
summarize a state value.

TIER-A STATE-VALUE CLASSIFICATION (a classification HINT — not a kind, not a pipeline)
Some statements are USER-PERSONAL-STATE facts: personal bests, schedules,
preferences (see the USER-PERSONAL-STATE VOCABULARY in the master list). For
these, the VALUE is the fact and must survive verbatim:
- Emit ONE statement Point with tier:"A".
- content = the fact WITH the exact value ("my personal best 5K time is
  27:12", NOT "user has a personal best 5K time").
- quote = the verbatim source sentence carrying the value (copy exactly,
  <=200 chars, from the story — S1 preserves facts exactly).
- pointKind stays "statement". tier is a hint only — no special kind, no
  special retrieval, no per-tier pipeline.
All other statement Points are tier:"B" (or omit tier).

ATOMIC POINTS (E3): emit ONE claim per point. Split compound statements
into separate points. The claim's VALUE survives verbatim — never compress
a concrete value ("27:12", "6pm") into a label ("the value"); the verbatim
value must be findable in the content or the point's `quote`.

SOURCE ATTRIBUTION (E3): for every point emit `quote` = the EXACT
conversation text the claim came from (verbatim, <=200 chars) and
`source_turn_id` = the {index}: marker from the SOURCE TRANSCRIPT that
asserted it. NEVER emit a speaker/role on the point — speaker is derived
at read time from the source turn's existing speaker/[role].

SEARCH KEYS (E3): emit 2-4 `search_keys` — paraphrases/synonyms a
questioner might use, plus the verbatim value tokens ("27:12",
"five-K time"). The fact is findable when asked with different words.

USER VS ASSISTANT (E3): an assistant suggestion/proposal is NOT a user
fact — do not emit it as a statement point unless the user confirmed it.

OUTPUT CONTRACT — ONE JSON object and NOTHING else:
{date_anchor}{output_contract}

Empty arrays are valid — extract-nothing is valid. Print ONLY the JSON object
(no markdown fences, no commentary)."""


def _render_source_transcript(edus: list[dict] | None) -> str:
    """E3 (D3): turn-indexed SOURCE TRANSCRIPT block for S2/S4 — lets the
    model cite `source_turn_id` from the {index}: markers instead of
    guessing. Over the char cap the block is omitted (quote-only resolution
    still anchors deterministically); edus=None renders byte-identically
    to today."""
    if not edus:
        return ""
    text = _edus_to_text(edus)
    if len(text) > _SOURCE_TRANSCRIPT_CAP:
        return ""  # over budget — quote-only resolution still works (D4)
    return ("SOURCE TRANSCRIPT (turn-indexed — cite source_turn_id from "
            "this; the numbers are the {index}: markers):\n" + text)


def render_s2_prompt(master: dict | None = None, *,
                     session_date: str | None = None,
                     edus: list[dict] | None = None) -> str:
    master = master or build_master_list()
    transcript = _render_source_transcript(edus)
    return (S2_TMPL
            .replace("{master_list}", _render_master(master))
            .replace("{chains_text}", _render_chains(master))
            .replace("{date_anchor}", _date_anchor(
                session_date, include_emission_rules=True))
            .replace("{output_contract}", OUTPUT_CONTRACT)
            + (("\n\n" + transcript) if transcript else ""))


def run_s2(model, story: str, master: dict | None = None, *,
           session_date: str | None = None,
           edus: list[dict] | None = None,
           stats: dict | None = None) -> dict:
    """S2: story → embed list (draft prompt v1, owner-in-the-loop pending).

    Output is bounded at ``_S2_S4_MAX_TOKENS`` (M3 #1524, D2); a truncated
    or unparseable response raises ``_ParseError`` → census ``parse_error``
    (the tail-cut tolerance of ``_parse_json`` still recovers truncated
    JSON; the census records the truncation for the fix loop)."""
    out = _complete(model,
                    render_s2_prompt(master, session_date=session_date,
                                     edus=edus),
                    "S1 STORY:\n" + story,
                    max_tokens=_stage_cap(_S2_S4_MAX_TOKENS), stats=stats)
    try:
        return _parse_json(out)
    except ValueError as e:
        raise _ParseError(str(e)) from e


# ── S3: SEARCH THE GRAPH (real backend, graceful degradation) ─────────────

def resolve_backend_mode() -> str:
    """'real' when a supported TORTOISE_DB_URI (docker:// / redis:// /
    rediss://) or a hosted API URL is configured; 'embedded' otherwise
    (FalkorDBLite — the test/eval-only store S3 must NOT read)."""
    import os  # noqa: I001
    from tortoise.config import is_db_uri
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri and is_db_uri(uri):
        return "real"
    if os.environ.get("TORTOISE_API_URL"):
        return "hosted"
    return "embedded"


def _story_topics(story: str, cap: int = 6) -> list[str]:
    """Deterministic topic queries from the story: first sentence of each
    paragraph, capped."""
    topics: list[str] = []
    for para in (story or "").split("\n\n"):
        first = _split_sentences(para)[:1]
        if first:
            topics.append(first[0][:120])
        if len(topics) >= cap:
            break
    return topics


def _derive_queries(embed_list: dict, story: str) -> dict:
    """Deterministic query derivation: entity names → object/subject,
    event contents → event, point contents → point, story topics → point.
    Non-dict array entries are skipped (model output can be malformed)."""
    queries: dict[str, list[str]] = {"object": [], "event": [], "point": []}
    seen: set[str] = set()

    def _add(entity_type: str, q: str) -> None:
        q = (q or "").strip()
        if not q:
            return
        # #1350 D3: RediSearch treats 'word:' as a field-prefix and
        # punctuation as syntax — sanitize derived query text so an FTS
        # failure never kills a search leg.
        q = re.sub(r"\b\w+:", " ", q)
        q = re.sub(r"[^\w\s-]", " ", q)
        q = re.sub(r"\s+", " ", q).strip()
        key = f"{entity_type}:{_norm_sent(q)}"
        if key in seen:
            return
        seen.add(key)
        queries[entity_type].append(q[:160])

    for e in embed_list.get("entities", []) or []:
        if isinstance(e, dict) and e.get("name"):
            _add("object", str(e["name"]))
    for ev in embed_list.get("events", []) or []:
        if isinstance(ev, dict) and ev.get("content"):
            _add("event", str(ev["content"])[:80])
    for p in embed_list.get("points", []) or []:
        if isinstance(p, dict) and p.get("content"):
            _add("point", str(p["content"])[:80])
    for t in _story_topics(story):
        _add("point", t)
    return queries


def _fts_rows(sdk, entity_type: str, query: str, limit: int = 3) -> list[dict]:
    rows = sdk.tortoise_fts_query(query, entity_type=entity_type, limit=limit)
    out = []
    for r in rows or []:
        if entity_type in ("object", "subject"):
            out.append({"id": r.get("id", ""), "name": r.get("content", ""),
                        "kind": r.get("kind", "")})
        else:
            out.append({"id": r.get("id", ""), "content": r.get("content", ""),
                        "kind": r.get("kind", "")})
    return out


def search_graph(sdk, embed_list: dict, story: str, *,
                 max_queries: int = 15, limit: int = 3) -> dict:
    """S3: search the REAL graph for existing entities/points/events.

    - Resolves the active backend from the environment (design doc §3 owner
      confirmation: NOT FalkorDBLite). Embedded → skip with a degraded flag.
    - Runs the same queries a client would: entity by name+kind, points by
      topic, events by entity (tortoise_fts_query, batch).
    - Graceful degradation: unreachable graph (connection error/timeout)
      returns partial results + ``degraded`` — the pipeline proceeds.

    Returns:
        {"mode": str, "degraded": bool, "reason": str|None,
         "entities": [{id, name, kind}], "points": [{id, content, kind}],
         "events": [{id, content, kind}], "queries_run": int}
    """
    mode = resolve_backend_mode()
    empty = {"mode": mode, "degraded": True, "reason": None,
             "entities": [], "points": [], "events": [], "queries_run": 0}
    if mode != "real":
        empty["reason"] = (f"S3 skipped: active backend is {mode!r} — the real "
                           "graph (FalkorDB via docker/redis URI or hosted API) "
                           "is required, not FalkorDBLite")
        return empty
    if sdk is None:
        empty["reason"] = "S3 skipped: no graph client (sdk) provided"
        return empty

    queries = _derive_queries(embed_list, story)
    # Bound the query budget deterministically.
    budget = max_queries
    bucket = {"object": "entities", "subject": "entities",
              "event": "events", "point": "points"}
    results: dict[str, dict] = {"entities": {}, "points": {}, "events": {}}
    q_run = 0
    try:
        first = True
        for entity_type, qs in queries.items():
            for q in qs:
                if q_run >= budget:
                    break
                q_run += 1
                try:
                    for row in _fts_rows(sdk, entity_type, q, limit=limit):
                        rid = row.get("id")
                        if not rid or rid in results[bucket[entity_type]]:
                            continue
                        results[bucket[entity_type]][rid] = row
                except Exception:
                    # FIRST-query failure = the backend is unreachable → the
                    # outer handler degrades the whole search. Later single-
                    # query failures are non-fatal (one bad query is not a
                    # dead graph).
                    if first:
                        raise
                    continue
                first = False
    except Exception as e:  # backend unreachable — degrade, don't raise
        return {
            "mode": mode, "degraded": True,
            "reason": f"S3 degraded: graph search failed ({type(e).__name__}: {e})",
            "entities": list(results["entities"].values()),
            "points": list(results["points"].values()),
            "events": list(results["events"].values()),
            "queries_run": q_run,
        }
    return {
        "mode": mode, "degraded": False, "reason": None,
        "entities": list(results["entities"].values()),
        "points": list(results["points"].values()),
        "events": list(results["events"].values()),
        "queries_run": q_run,
    }


def _render_search_results(search: dict) -> str:
    if not search:
        return "(no graph search results)"
    if search.get("degraded"):
        return (f"(graph search DEGRADED — {search.get('reason') or 'skipped'}; "
                "no existing items available; assume everything is new)")
    parts = []
    if search.get("entities"):
        parts.append("EXISTING ENTITIES (id | name | kind):\n" + "\n".join(
            f"- {e['id']} | {e.get('name', '')} | {e.get('kind', '')}"
            for e in search["entities"][:40]))
    if search.get("points"):
        parts.append("EXISTING POINTS (id | content | kind):\n" + "\n".join(
            f"- {p['id']} | {p.get('content', '')[:120]} | {p.get('kind', '')}"
            for p in search["points"][:40]))
    if search.get("events"):
        parts.append("EXISTING EVENTS (id | content | kind):\n" + "\n".join(
            f"- {e['id']} | {e.get('content', '')[:120]} | {e.get('kind', '')}"
            for e in search["events"][:40]))
    if not parts:
        return "(graph search returned no existing items)"
    return "\n\n".join(parts)


# ── S4: REVIEW GAPS — the complete embed list ──────────────────────────────

S4_TMPL = """You are the GAP REVIEWER for the Tortoise epistemic memory.

TASK
You have: (a) the compiled story of the conversation, (b) the S2 embed list,
(c) the results of searching the existing graph. Review for GAPS: did S2 miss
any key entities, events, or points that AFFECT THE WORLD MODEL — durable
objects/subjects, decisions/occurrences, claims whose support/attack
structure matters, AND durable operational/process lessons (cause-effect
knowledge about how the environment behaves or how the team works — e.g.
"backgrounded processes die when the tool returns", "create_point defaults to
draft mode")? Add them. Do NOT pad with process chatter (the value filter
applies — same STRICT EXCLUSION as S2: strip the mechanics tokens, keep the
durable claim — with the same CARVE-OUT: user-personal-state VALUES (personal
bests, schedules, preferences) are facts, kept verbatim, never treated as
mechanics tokens).

MASTER LIST (same closed vocabulary as S2 — no minted kinds)
{master_list}

CHAINS
{chains_text}

S1 STORY
{story}

S3 GRAPH SEARCH RESULTS
{search_results}

S2 EMBED LIST (may be incomplete — that is why you exist)
{embed_list_json}

OUTPUT — ONE JSON object, the COMPLETE embed list (S2 + gaps), SAME contract:
{date_anchor}{output_contract}

Rules:
- Re-emit the S2 items you keep, corrected where the search results show they
  already exist (lifecycle changed/unchanged + supersedes = the existing id).
- SUPERSESSION (state objects/subjects): when the story shows an entity
  REPLACED ("adopted B over A", "drop the old approach") and the search
  results contain the superseded item, emit the NEW entity with
  "supersedes": "<existing-id>"; the old entity is NOT re-created; anaphora
  ("the old strategy") resolves to the existing item. Emit ONE statement
  point capturing the replacement wired to BOTH entities (about_entities =
  [new, superseded]).
- DECISION EVENTS only when a real decision exists — never fabricate one for
  a supersession or completion.
- A point that already exists in the graph (same content) → lifecycle
  unchanged, do NOT re-create.
- PARTICIPANT SLOTS (typed roles, #1418): keep/correct every "slots" block
  from S2 (subject/object/event roles, each entry {"name", "kind",
  "confidence"}) and ADD missing slots where the search results identify
  the participant. Kind from the MASTER LIST — no minted kinds. subject/
  object names MUST reference an entity in the entities[] of THIS output
  (unresolvable refs are dropped at execution). The event slot is event-as-
  content (#1417): an event the content is ABOUT, never a provenance/
  capture event.
- ATOMIC POINTS (E3): emit ONE claim per point. Split compound statements
  into separate points. The claim's VALUE survives verbatim — never compress
  a concrete value ("27:12", "6pm") into a label ("the value"); the verbatim
  value must be findable in the content or the point's `quote`.
- SOURCE ATTRIBUTION (E3): for every point emit `quote` = the EXACT
  conversation text the claim came from (verbatim, <=200 chars) and
  `source_turn_id` = the {index}: marker from the SOURCE TRANSCRIPT that
  asserted it. NEVER emit a speaker/role on the point — speaker is derived
  at read time from the source turn's existing speaker/[role].
- SEARCH KEYS (E3): emit 2-4 `search_keys` — paraphrases/synonyms a
  questioner might use, plus the verbatim value tokens ("27:12",
  "five-K time"). The fact is findable when asked with different words.
- USER VS ASSISTANT (E3): an assistant suggestion/proposal is NOT a user
  fact — do not emit it as a statement point unless the user confirmed it.
- OPERATOR REFERENCING: operator src/dst MUST be the EXACT content of a point
  or event emitted in THIS output (copy verbatim, no paraphrasing). If an
  endpoint has no point yet, CREATE the point first. NEVER use an entity name
  as an operator endpoint — entities wire via about_entities.
- MITIGATES: relevance attack on the OPERATOR edge, strength 0.10-0.50.
  NAND: truth attack on a FACTUALLY WRONG point. Golden rule: relevance lives
  on the OPERATOR, truth lives on the POINT. Never NAND an option/criterion
  for being a bad fit.
- chain_notes: flag violations, TRY TO REPAIR toward the nearest valid chain
  position, never invent entities.
- link_before_create: record what you searched / what the graph already had.
- Tier-A state-value points from the S2 list are NEVER dropped — re-emit
  them (with corrections if the search shows they exist). The S4 pass
  COMPLETES the list; it does not replace S2 findings. The value is the fact.
Empty arrays are valid. Print ONLY the JSON object."""


def render_s4_prompt(story: str, search: dict, embed_list: dict,
                     master: dict | None = None, *,
                     session_date: str | None = None,
                     edus: list[dict] | None = None) -> str:
    master = master or build_master_list()
    transcript = _render_source_transcript(edus)
    return (S4_TMPL
            .replace("{master_list}", _render_master(master))
            .replace("{chains_text}", _render_chains(master))
            .replace("{story}", story)
            .replace("{search_results}", _render_search_results(search))
            .replace("{embed_list_json}", json.dumps(embed_list, indent=1))
            .replace("{date_anchor}", _date_anchor(
                session_date, include_emission_rules=True))
            .replace("{output_contract}", OUTPUT_CONTRACT)
            + (("\n\n" + transcript) if transcript else ""))


def run_s4(model, story: str, search: dict, embed_list: dict,
           master: dict | None = None, *,
           session_date: str | None = None,
           edus: list[dict] | None = None,
           stats: dict | None = None) -> dict:
    """S4: complete the embed list (S2 + gaps). Draft prompt v1.

    Output bounded at ``_S2_S4_MAX_TOKENS`` (M3 #1524, D2); unparseable
    output → ``_ParseError`` → census ``parse_error`` (see ``run_s2``)."""
    out = _complete(model,
                    render_s4_prompt(story, search, embed_list, master,
                                     session_date=session_date, edus=edus),
                    "Complete the embed list.",
                    max_tokens=_stage_cap(_S2_S4_MAX_TOKENS), stats=stats)
    try:
        return _parse_json(out)
    except ValueError as e:
        raise _ParseError(str(e)) from e


# ── S5: EMBED — deterministic execution → Layer-1 payload ──────────────────

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _norm_kind(k: str) -> str:
    """Bare + case-folded kind form — the link-before-create lookup key.
    The model may emit 'plan' where the backend stores 'core:plan'; both
    must resolve to the same key or link-before-create misses and a
    duplicate :Object is created server-side."""
    return str(k or "").strip().rsplit(":", 1)[-1].lower()


# E5 (#1537) — a single shared content token is never a revision; the
# length guard ratio is against the LONGER side (max-denominator).
_MIN_OVERLAP_TOKENS = 2

# E5 fact-value contradiction frame: stopword-stripped shared tokens on the
# longer side must reach 0.5. A small LOCAL closed-class set (importing the
# eval's ingest_v2._STOPWORDS into tortoise/ would invert the layering).
_FRAME_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "of",
    "to", "in", "on", "at", "for", "with", "from", "by", "about",
    "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "it", "this", "that", "these", "those", "i", "we", "you",
    "he", "she", "they", "me", "my", "our", "your", "their", "not",
    "no", "yes", "so", "as", "than", "now", "today", "yesterday",
})


def _frame_tokens(content: str) -> set[str]:
    """Stopword-stripped content tokens — the shared non-value frame."""
    return {t for t in _norm(content).split() if t not in _FRAME_STOPWORDS}


def _fact_value_contradiction(content: str, about_entities: list[str] | None,
                              existing: dict, *, when: str | None = None) -> bool:
    """Entity-grounded fact-value contradiction (E5 #1537, DD-2).

    True when the new point contradicts the existing point's VALUE for the
    same entity while keeping the non-value frame:
      1. same entity — the new point's about_entities name(s) co-mention in
         the existing point's content (content proxy; S3 point rows carry
         {id, content, kind} only — CG-3),
      2. attribute frame — stopword-stripped token overlap on the LONGER
         side >= 0.5 (the shared non-value frame),
      3. value differs — the new content has at least one token the old
         lacks (identical-value re-assertions are deduped by the exact-match
         pass before this runs; the value-diff also rejects them directly),
      4. later date — when BOTH sides carry a date (``when`` = the new
         point's E1 ISO date; existing.when/createdAt = the old's), the new
         must be >= the old; absent on either side → session-ingest order
         supplies the invariant (CG-2).
    """
    old_content = str(existing.get("content", "")) or ""
    old_norm = _norm(old_content)
    if not old_norm:
        return False
    # 1. same entity (content proxy for the aboutObject edge — CG-3)
    entities = [str(a).strip().lower() for a in (about_entities or [])
                if isinstance(a, str) and a.strip()]
    if entities and not any(e in old_norm for e in entities):
        return False
    # 2. attribute frame on the LONGER side
    t_new = _frame_tokens(content)
    t_old = _frame_tokens(old_content)
    if not t_new or not t_old:
        return False
    shared = t_new & t_old
    if not shared or len(shared) / max(len(t_new), len(t_old)) < 0.5:
        return False
    # 3. value differs — at least one token the old lacks
    if not (t_new - t_old):
        return False
    # 4. later-date guard — dormant when either side is undated (CG-2)
    old_when = str(existing.get("when") or existing.get("createdAt") or "").strip()
    if when and old_when:
        try:
            if _valid_iso_date(when) and _valid_iso_date(old_when):
                if when[:10] < old_when[:10]:
                    return False
        except (TypeError, ValueError):
            pass  # junk date → treat as undated (session-order invariant)
    return True


def _token_overlap(a: str, b: str) -> float:
    ta = set(_norm(a).split())
    tb = set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    if shared < _MIN_OVERLAP_TOKENS:
        # a single shared content token is never a revision (floor-2 guard)
        return 0.0
    # length-guard: ratio against the LONGER side — a 5-token point sharing
    # 3 tokens with a 50-token point is 3/50 = 0.06, not 3/5 = 0.6 (the
    # false-REVISES ≥ 0.6 bug, E5 #1537 / E2E-6 negative)
    return shared / max(len(ta), len(tb))


def _source_turn_overlap(turn: str, quote: str) -> float:
    """E3 source-turn paraphrase overlap — MIN-denominator.

    The E5 length guard (max-denominator + floor-2) fixes point-to-point
    REVISES, but the E3 quote→turn fallback needs the short-quote-sensitive
    ratio: a 2-token quote "speed intervals" paraphrasing a 5-token turn
    shares 2 tokens and must score 2/2 = 1.0 (≥ 0.6 → resolves), not 2/5 =
    0.4 (E3 #1535 contract; kept separate so the two semantics don't
    couple).
    """
    ta = set(_norm(turn).split())
    tb = set(_norm(quote).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _content_id(prefix: str, content: str) -> str:
    from tortoise.ids import content_hash
    return f"{prefix}_{content_hash(content)[:62]}"


def _clean_slots(raw, warnings: list[str], ctx: str,
                 master: dict | None = None) -> dict | None:
    """Sanitize LLM-emitted participant slots (#1418) into the Layer-1
    shape {subject/object/event: [{name, kind, confidence}]}.

    Deterministic and CARRY-ONLY — this never binds (threshold gating is
    the #1370 write path's job): non-dict entries dropped, blank names/
    kinds dropped, minted kinds repaired to the family fallback (the same
    master_kind_forms gate S5 applies to entities/events — subject/object
    kinds gate against the entity vocabulary, event kinds against the event
    vocabulary), confidence coerced to float and clamped to [0,1]
    (non-numeric → 0.0), unknown role keys and non-list role values dropped
    with a warning. Returns None when no role survived (the payload entry
    gets no slots).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        warnings.append(f"{ctx}: slots must be an object — dropped")
        return None
    master = master or {}
    entity_forms = master_kind_forms(master) if master else None
    event_forms = {k.lower() for k in master.get("events", {})}
    event_forms_bare = {k.lower().rsplit(":", 1)[-1]
                        for k in master.get("events", {})}
    out: dict[str, list[dict]] = {}
    for role in ("subject", "object", "event"):
        raw_refs = raw.get(role)
        if raw_refs is None:
            continue
        if not isinstance(raw_refs, list):
            warnings.append(f"{ctx}: slot role {role!r} must be a list — "
                            "dropped")
            continue
        refs: list[dict] = []
        for r in raw_refs:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name", "")).strip()
            kind = str(r.get("kind", "")).strip()
            if not name or not kind:
                continue
            if role == "event":
                if kind.lower() not in event_forms and \
                        kind.lower().rsplit(":", 1)[-1] not in event_forms_bare:
                    warnings.append(f"minted slot kind {kind!r} ('{name[:60]}'"
                                    f") → repaired to {_EVENT_FALLBACK['kind']}")
                    kind = _EVENT_FALLBACK["kind"]
            elif entity_forms and \
                    kind.lower() not in entity_forms and \
                    kind.lower().rsplit(":", 1)[-1] not in {
                        f.lower().rsplit(":", 1)[-1] for f in entity_forms}:
                warnings.append(f"minted slot kind {kind!r} ('{name[:60]}'"
                                f") → repaired to {_ENTITY_FALLBACK['kind']}")
                kind = _ENTITY_FALLBACK["kind"]
            try:
                conf = float(r.get("confidence"))
            except (TypeError, ValueError):
                conf = 0.0
            refs.append({"name": name, "kind": kind,
                         "confidence": min(1.0, max(0.0, conf))})
        if refs:
            out[role] = refs
    for k in raw:
        if k not in ("subject", "object", "event"):
            warnings.append(f"{ctx}: unknown slot role {k!r} dropped")
    return out or None


def _resolve_slot_refs(slots: dict | None, entity_keys: set,
                       warnings: list[str], ctx: str) -> dict | None:
    """Filter participant slots to resolvable references (#1418, review fix).

    subject/object slots must resolve to an EMITTED entity (name, bare
    kind) — a stray slot must never sink the whole commit (the
    operator-drop pattern; Layer-1's (name, kind) gate then never fires on
    S5 output). event slots pass through untouched: they are event-as-
    content (#1417 B2) and the #1370 write path resolves them against
    events.
    """
    if not slots:
        return slots
    out: dict[str, list[dict]] = {}
    for role, refs in slots.items():
        if role == "event":
            out[role] = refs
            continue
        kept = []
        for r in refs:
            if (r["name"], _norm_kind(r["kind"])) in entity_keys:
                kept.append(r)
            else:
                warnings.append(
                    f"{ctx}: slot {role} {r['name']!r} (kind {r['kind']!r}) "
                    "does not resolve to an emitted entity — dropped "
                    "(fail-closed: the write path binds emitted entities)")
        if kept:
            out[role] = kept
    return out or None


def _index_search(search: dict) -> dict:
    """Deterministic existing-graph index from the S3 results (the
    link-before-create surface). Entities keep their RAW kind so lookups can
    try exact (namespace-preserved) first, then bare-form fallback."""
    idx = {"entities": [], "points": [], "events": []}
    for e in (search or {}).get("entities", []) or []:
        if isinstance(e, dict) and e.get("name"):
            idx["entities"].append(e)
    for p in (search or {}).get("points", []) or []:
        if isinstance(p, dict) and p.get("content"):
            idx["points"].append(p)
    for ev in (search or {}).get("events", []) or []:
        if isinstance(ev, dict) and ev.get("content"):
            idx["events"].append(ev)
    return idx


def _find_existing_entity(entities: list[dict], name: str, kind: str) -> tuple[dict | None, str]:
    """(existing, mode) — exact kind match first (namespace-preserved, case-
    folded); bare-form fallback ONLY when unambiguous; 'ambiguous' when
    multiple namespaces collide on the bare form (never guess which one)."""
    norm_name = _norm(name)
    kind_folded = str(kind).strip().lower()
    exact = [e for e in entities
             if _norm(e.get("name", "")) == norm_name
             and str(e.get("kind", "")).strip().lower() == kind_folded]
    if exact:
        return exact[0], "exact"
    bare = _norm_kind(kind)
    matches = [e for e in entities
               if _norm(e.get("name", "")) == norm_name
               and _norm_kind(e.get("kind", "")) == bare]
    if len(matches) == 1:
        return matches[0], "bare"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "none"


def _find_point_match(points: list[dict], content: str, *,
                       about_entities: list[str] | None = None,
                       when: str | None = None) -> tuple[str, str]:
    """(match_kind, existing_id) — 'exact' (same id, dedup), 'revises'
    (supersedes by correction — new content-addressed id), or ('none', '').

    Third pass (E5 #1537): entity-grounded fact-value contradictions that
    the length-guarded overlap misses ("gym 6pm" → "gym 5pm": 1 shared
    token) still REVISES when the new point's about_entities co-mention in
    the existing point's content, the non-value frame overlaps ≥ 0.5, and
    the value actually differs. ``when`` (E1) is the new point's ISO date —
    when BOTH sides carry a date the new must be >= the old (CG-2); when
    either is absent the session-ingest order supplies the invariant.
    """
    norm = _norm(content)
    for p in points:
        if _norm(p.get("content", "")) == norm:
            return "exact", p.get("id", "")
    for p in points:
        if _token_overlap(p.get("content", ""), content) >= 0.6:
            return "revises", p.get("id", "")
    for p in points:
        if _fact_value_contradiction(content, about_entities, p, when=when):
            return "revises", p.get("id", "")
    return "none", ""


def _chain_positions(master: dict) -> dict[str, tuple[str, int]]:
    """kind → (chain, position) for every kind in every chain path."""
    pos: dict[str, tuple[str, int]] = {}
    for name, c in master.get("chains", {}).items():
        for i, kind in enumerate(c.get("path", [])):
            pos[kind.lower()] = (name, i)
            bare = kind.rsplit(":", 1)[-1].lower()
            pos[bare] = (name, i)
    return pos


def validate_chains(embed_list: dict, master: dict | None = None) -> list[dict]:
    """Deterministic chain validation (design doc §7.4): WARN, then TRY TO
    REPAIR toward the nearest valid chain position; hard-block never.

    Checks entity co-mentions in about_entities: when two kinds belong to the
    SAME chain and the pair is listed in reverse chain order, that is a
    direct-edge violation (e.g. a customer connected straight to an
    architecture requirement). When the nearest intermediate kind already
    exists in the embed list's entities, the fix is possible → action
    'repaired' (connect via the intermediate); otherwise 'warned'.
    """
    master = master or build_master_list()
    pos = _chain_positions(master)
    entity_kinds: dict[str, str] = {}
    for e in embed_list.get("entities", []) or []:
        if isinstance(e, dict) and e.get("name") and e.get("kind"):
            entity_kinds[_norm(e["name"])] = str(e["kind"])
    notes: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in (embed_list.get("points", []) or []) + (embed_list.get("events", []) or []):
        if not isinstance(item, dict):
            continue
        about = item.get("about_entities") or []
        if not isinstance(about, list):
            continue
        names = [_norm(a) for a in about if isinstance(a, str)]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                ka = entity_kinds.get(names[i])
                kb = entity_kinds.get(names[j])
                if not ka or not kb:
                    continue
                pa = pos.get(ka.rsplit(":", 1)[-1].lower())
                pb = pos.get(kb.rsplit(":", 1)[-1].lower())
                if not pa or not pb or pa[0] != pb[0]:
                    continue
                if pa[1] <= pb[1]:
                    continue  # chain order OK
                chain = pa[0]
                key = (names[i], names[j], chain)
                if key in seen:
                    continue
                seen.add(key)
                path = [k.rsplit(":", 1)[-1] for k in
                        master["chains"][chain]["path"]]
                path_lower = [p.lower() for p in path]
                a_i, b_i = pa[1], pb[1]
                intermediate = None
                # nearest valid position between b and a, from the LIST.
                for e in embed_list.get("entities", []) or []:
                    if not isinstance(e, dict):
                        continue
                    k = str(e.get("kind", "")).rsplit(":", 1)[-1].lower()
                    if k in path_lower and b_i < path_lower.index(k) < a_i:
                        intermediate = str(e.get("name"))
                        break
                if intermediate:
                    notes.append({
                        "chain": chain,
                        "finding": (f"'{names[i]}' ({ka}) connects to "
                                    f"'{names[j]}' ({kb}) in reverse chain "
                                    f"order ({' → '.join(path)})"),
                        "action": "repaired",
                        "note": f"re-map the connection via '{intermediate}' "
                                f"(nearest valid chain position in the list)",
                    })
                else:
                    notes.append({
                        "chain": chain,
                        "finding": (f"'{names[i]}' ({ka}) connects to "
                                    f"'{names[j]}' ({kb}) in reverse chain "
                                    f"order ({' → '.join(path)})"),
                        "action": "warned",
                        "note": "no intermediate chain kind present — flag, "
                                "do NOT invent entities to satisfy the chain",
                    })
    return notes


def _minted_kind_report(embed_list: dict, master: dict | None = None) -> list[str]:
    """Every kind used in entities/events/points that is NOT in the master
    list (indicator: 0 minted kinds)."""
    master = master or build_master_list()
    forms = master_kind_forms(master)
    full = {k.lower() for k in forms if ":" in k}
    bare = {k.lower().rsplit(":", 1)[-1] for k in forms}
    minted: list[str] = []
    for e in embed_list.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        k = str(e.get("kind", ""))
        if k and k.lower() not in full and k.lower() not in bare:
            minted.append(f"{k} (entity '{e.get('name', '')[:60]}')")
    for ev in embed_list.get("events", []) or []:
        if not isinstance(ev, dict):
            continue
        k = str(ev.get("eventKind", ""))
        if k and k.lower() not in full and k.lower() not in bare:
            minted.append(f"{k} (event '{ev.get('content', '')[:60]}')")
    for p in embed_list.get("points", []) or []:
        if not isinstance(p, dict):
            continue
        k = str(p.get("pointKind", ""))
        if k and k.lower() not in full and k.lower() not in bare:
            minted.append(f"{k} (point '{p.get('content', '')[:60]}')")
    return minted


def _resolve_superseded(ref: str, search: dict, *, kind: str = "") -> dict | None:
    """Resolve an entity ``supersedes`` ref to the S3-returned existing
    ENTITY dict: by id (content-addressed, unique), or by name filtered to
    the SAME kind (a name may collide across kinds — never resolve to the
    wrong kind's entity). Ambiguous (multiple same-kind matches) → None —
    never guesses. Returns the entity dict so the caller can use its id,
    name, and kind for identity checks."""
    ref = (ref or "").strip()
    if not ref or ref in ("null", "None"):
        return None
    entities = [e for e in (search or {}).get("entities", []) or []
                if isinstance(e, dict)]
    for e in entities:
        if e.get("id") == ref:
            return e
    matches = [e for e in entities
               if _norm(e.get("name", "")) == _norm(ref)
               and (not kind or _norm_kind(e.get("kind", "")) == _norm_kind(kind))]
    if len(matches) == 1:
        return matches[0]
    return None  # 0 or >1 → caller warns, never guesses


def _supersession_records(entity_refs: list[dict], search: dict,
                          *, warnings: list | None = None) -> list[dict]:
    """Shared supersession-record builder — the ONE resolution discipline for
    both execute_embed's recording and derive_supersessions (they cannot
    diverge). Each ref: {"name", "kind", "supersedes"}. Self-supersession
    (the ref resolves to the new entity ITSELF — same name AND kind) is
    skipped with a warning; records are deduped by (superseded, supersedes_by)."""
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for e in entity_refs:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name", "")).strip()
        kind = str(e.get("kind", "")).strip()
        ref = str(e.get("supersedes") or "").strip()
        if not name or not ref or ref in ("null", "None"):
            continue
        existing = _resolve_superseded(ref, search, kind=kind)
        if not existing:
            if warnings is not None:
                warnings.append(f"entity '{name[:60]}' supersedes={ref!r} does not "
                                "resolve to an S3 search result — recorded without "
                                "a graph id")
            continue
        if (_norm(existing.get("name", "")) == _norm(name)
                and _norm_kind(existing.get("kind", "")) == _norm_kind(kind)):
            if warnings is not None:
                warnings.append(f"entity '{name[:60]}' supersedes itself "
                                f"({ref!r}) — skipped")
            continue
        display = str(existing.get("id")) or str(existing.get("name"))
        key = (display, name)
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "superseded": display, "supersedes_by": name,
            "evidence": "entity lifecycle supersedes (conversation-driven)",
        })
    return records


def derive_supersessions(embed_list: dict, search: dict) -> list[dict]:
    """The minimal status-derivation mapping (state-centric model): from the
    embed list's entity lifecycle/supersedes + the S3 search results, derive
    'entity A superseded by entity B' pairs. This is the read-side
    projection's input — the event stream (session event + filed points) is
    the truth; object status is derived, not stored. Shares the resolution
    discipline with execute_embed's recording (same helper).

    Returns [{"superseded": ..., "supersedes_by": ..., "evidence": ...}].
    """
    refs = [{"name": str(e.get("name", "")).strip(),
             "kind": str(e.get("kind", "")).strip(),
             "supersedes": str(e.get("supersedes") or "").strip()}
            for e in (embed_list.get("entities", []) or [])
            if isinstance(e, dict)]
    return _supersession_records(refs, search)


def _clean_search_keys(raw, warnings: list[str], ctx: str) -> list[str]:
    """E3: 2-4 search_key aliases + verbatim tokens — sanitize to a deduped
    list of 1-60-char strings (max 4). LLM output is advisory; malformed
    entries are dropped, not fatal."""
    out: list[str] = []
    if raw is None:
        return out
    if not isinstance(raw, list):
        warnings.append(f"{ctx}: search_keys must be a list — dropped")
        return out
    for k in raw:
        k = str(k).strip()
        if not k:
            continue
        if len(k) > 60:
            warnings.append(f"{ctx}: search_keys entry >60 chars dropped")
            continue
        if k not in out:
            out.append(k)
    if len(out) > 4:
        warnings.append(f"{ctx}: search_keys capped at 4 (had {len(out)})")
        out = out[:4]
    return out


def _resolve_source_turn(p: dict, edus: list[dict] | None,
                         *, warnings: list[str]) -> int | None:
    """E3: the authoritative source-turn index for a point. The verbatim
    quote is the anchor; the model's source_turn_id is advisory (a wrong
    model index must never win — mirror of the never-guess discipline)."""
    if not edus:
        return None
    quote = re.sub(r"\s+", " ", str(p.get("quote") or "").strip().lower())
    model_idx = p.get("source_turn_id")

    def _contains(text: str) -> bool:
        if not quote:
            return False
        return re.sub(r"\s+", " ", str(text).lower()).find(quote) >= 0

    # 1) verbatim anchor — first turn containing the quote
    det_idx = next((e["index"] for e in edus if _contains(e.get("text", ""))),
                   None)
    # 2) model index in range? (type() is int — a JSON boolean `true` is an
    # int subclass and must NOT be treated as index 1: never guess). Resolved
    # by INDEX VALUE, not list position — _edus_from_conversation drops
    # empty-content turns while preserving original indices, so position and
    # value can diverge.
    if type(model_idx) is int and 0 <= model_idx < len(edus):
        m_turn = next((e.get("text", "") for e in edus
                       if e["index"] == model_idx), "")
        # Contradiction fires only when the model's named turn EXISTS and
        # disagrees with the quote anchor (m_turn non-empty guard). An index
        # that names no turn (dropped empty-content turn) has nothing to
        # contradict — the deterministic anchor wins silently.
        if det_idx is not None and m_turn and not _contains(m_turn):
            warnings.append(f"source_turn_id {model_idx} contradicts the quote's "
                            f"turn {det_idx} — deterministic match wins")
        # D4 step 3: quote empty but a plausible index present → use it, but
        # warn "unverified" (never silently trust an unanchored model index)
        elif det_idx is None and not quote and m_turn:
            warnings.append(f"source_turn_id {model_idx} unverified "
                            "(empty quote) — using it")
            return model_idx
    if det_idx is not None:
        return det_idx
    # 3) no verbatim match → token-overlap fallback (single best >= 0.6)
    best, best_ov = None, 0.0
    for e in edus:
        ov = _source_turn_overlap(str(e.get("text", "")),
                                  str(p.get("quote") or ""))
        if ov > best_ov:
            best, best_ov = e["index"], ov
    if best is not None and best_ov >= 0.6:
        return best
    # 4) no anchor at all — fail-open, never guess
    warnings.append(f"point '{str(p.get('content', ''))[:40]}' has no resolvable "
                    "source turn (no quote match)")
    return None


_ENTITY_FALLBACK = {"kind": "core:other"}
_EVENT_FALLBACK = {"kind": "core:occurrence"}
_POINT_FALLBACK = {"kind": "statement"}


def execute_embed(embed_list: dict, search: dict, *, session_id: str,
                  story_arc: str = "", summary: str = "",
                  extractor_version: str = "value@0.5.0+v2",
                  master: dict | None = None,
                  session_date: str | None = None,
                  edus: list[dict] | None = None) -> dict:
    """S5: deterministic embed EXECUTION (not a flash prompt).

    Maps the complete embed list to the Layer-1 commit payload in dependency
    order (entities → events → points → operators), honoring:
      - link-before-create: search results index every candidate (dedup).
      - supersession: points that revise an existing topic → reason REVISES.
      - chain warn+repair: validate_chains (never hard-block).
      - no minted kinds: unknown kinds repair to the family fallback with a
        warning (core:other / core:occurrence / statement).
      - Layer-1 integrity: operators whose src/dst/target reference no emitted
        point/event are DROPPED with a warning (mirrors _stream_to_payload);
        MITIGATES strengths clamp to [0.10, 0.50] with a warning.

    Returns {"payload", "chain_notes", "link_before_create", "warnings",
             "minted_kinds", "stats"}.
    """
    master = master or build_master_list()
    idx = _index_search(search)
    warnings: list[str] = []

    # ── entities (dependency order 1) ─────────────────────────────────────
    payload_entities: list[dict] = []
    link_before_create: list[dict] = []
    entity_supersede_refs: list[dict] = []   # for the supersession recording
    supersessions: list[dict] = []           # conversation-driven supersession records
    seen_entities: set[tuple[str, str]] = set()
    for e in embed_list.get("entities", []) or []:
        if not isinstance(e, dict):
            warnings.append(f"non-dict entity entry {e!r} skipped")
            continue
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        kind = str(e.get("kind", "")).strip() or "core:other"
        forms = master_kind_forms(master)
        if kind.lower() not in forms and \
                kind.lower().rsplit(":", 1)[-1] not in {f.lower().rsplit(":", 1)[-1]
                                                        for f in forms}:
            warnings.append(f"minted entity kind {kind!r} ('{name[:60]}') "
                            f"→ repaired to {_ENTITY_FALLBACK['kind']}")
            kind = _ENTITY_FALLBACK["kind"]
        key = (name, kind)
        if key in seen_entities:
            continue
        seen_entities.add(key)
        existing, match_mode = _find_existing_entity(
            idx["entities"], name, kind)
        if existing:
            link_before_create.append({
                "searched_for": f"entity '{name}'", "found": True,
                "note": f"existing {existing.get('id', '')} — connect, do not re-create"})
        else:
            if match_mode == "ambiguous":
                warnings.append(f"entity '{name[:60]}' kind '{kind}' is ambiguous "
                                "across namespaces in the existing graph — "
                                "created with the explicit kind (link-before-"
                                "create cannot disambiguate)")
            link_before_create.append({
                "searched_for": f"entity '{name}'", "found": False,
                "note": "no match — created"})
        lifecycle = str(e.get("lifecycle", "") or "").strip()
        supersedes_ref = str(e.get("supersedes") or "").strip()
        if lifecycle in ("changed", "superseded"):
            warnings.append(f"entity '{name[:60]}' lifecycle={lifecycle} is not "
                            "expressible in the Layer-1 payload — the server "
                            "merges entities by (name, kind); the state change "
                            "must ride on points/events instead")
        if supersedes_ref and supersedes_ref not in ("null", "None", ""):
            entity_supersede_refs.append({"name": name, "kind": kind,
                                          "supersedes": supersedes_ref})
        payload_entities.append({
            "name": name, "kind": kind, "passes_frequency_gate": True})

    # conversation-driven supersession: deterministic recording (the shared
    # resolver validates against S3 by id/kind-filtered name — never guesses)
    supersessions = _supersession_records(entity_supersede_refs, search,
                                          warnings=warnings)
    # the emitted-entity key set — participant slots (subject/object) must
    # resolve against it (#1418, fail-closed; the write path binds emitted
    # entities only)
    emitted_entity_keys = {(e["name"], _norm_kind(e["kind"]))
                           for e in payload_entities}

    # ── events (dependency order 2) ───────────────────────────────────────
    payload_events: list[dict] = []
    event_ids: dict[str, str] = {}   # norm content → event id
    event_contents: set[str] = set()
    for ev in embed_list.get("events", []) or []:
        if not isinstance(ev, dict):
            warnings.append(f"non-dict event entry {ev!r} skipped")
            continue
        content = str(ev.get("content", "")).strip()[:1000]
        if not content:
            continue
        ekind = str(ev.get("eventKind", "")).strip() or "core:occurrence"
        if ekind.lower() not in {k.lower() for k in master["events"]} and \
                ekind.lower().rsplit(":", 1)[-1] not in {k.lower().rsplit(":", 1)[-1]
                                                         for k in master["events"]}:
            warnings.append(f"minted event kind {ekind!r} ('{content[:60]}') "
                            f"→ repaired to {_EVENT_FALLBACK['kind']}")
            ekind = _EVENT_FALLBACK["kind"]
        n = _norm(content)
        if n in event_contents:
            continue
        event_contents.add(n)
        eid = _content_id("ev", content)
        for ex in idx["events"]:
            if _norm(ex.get("content", "")) == n:
                eid = str(ex.get("id", "")) or eid
                link_before_create.append({
                    "searched_for": f"event '{content[:60]}'", "found": True,
                    "note": f"existing {eid} — connect, do not re-create"})
                break
        else:
            link_before_create.append({
                "searched_for": f"event '{content[:60]}'", "found": False,
                "note": "no match — created"})
        event_ids[n] = eid
        ev_entry = {
            "id": eid, "eventKind": ekind.rsplit(":", 1)[-1] if ":" in ekind else ekind,
            "content": content, "confidence": 0.5,
            "about_entities": [str(a) for a in (ev.get("about_entities") or [])
                               if isinstance(a, str) and a.strip()],
            "source_ref": "session.md",
        }
        ev_slots = _clean_slots(ev.get("slots"), warnings,
                                f"event '{content[:60]}'", master)
        ev_slots = _resolve_slot_refs(ev_slots, emitted_entity_keys, warnings,
                                      f"event '{content[:60]}'")
        if ev_slots:
            ev_entry["slots"] = ev_slots
        # E1 (#1533) — events are time-bound: default to the session date when
        # the model omitted startedAt (dated session ⇒ every event dated);
        # junk dates are dropped with a warning. Undated session ⇒ no key
        # (payload stays byte-identical).
        started_at = str(ev.get("startedAt") or "").strip()
        if not started_at and session_date:
            started_at = session_date
        if started_at:
            if _valid_iso_date(started_at):
                ev_entry["started_at"] = started_at
            else:
                warnings.append(
                    f"event startedAt {started_at!r} is not a valid ISO date "
                    "— dropped")
        payload_events.append(ev_entry)

    # ── points (dependency order 3) ───────────────────────────────────────
    payload_points: list[dict] = []
    point_ids: dict[str, str] = {}   # norm content → point id
    tier_a_points = 0                # E2 (#1534): Tier-A state-value count
    for p in embed_list.get("points", []) or []:
        if not isinstance(p, dict):
            warnings.append(f"non-dict point entry {p!r} skipped")
            continue
        content = str(p.get("content", "")).strip()[:1000]
        if not content:
            continue
        pkind = str(p.get("pointKind", "")).strip() or "statement"
        if pkind.lower() not in {k.lower() for k in master["points"]}:
            warnings.append(f"minted point kind {pkind!r} ('{content[:60]}') "
                            f"→ repaired to {_POINT_FALLBACK['kind']}")
            pkind = _POINT_FALLBACK["kind"]
        n = _norm(content)
        if n in point_ids:
            continue
        # E1 (#1533) — the point's `when` slot (used by the E5 later-date
        # guard, CG-2): validate here, once, before the match passes.
        when = str(p.get("when") or "").strip()
        when_valid = when if (when and _valid_iso_date(when)) else None
        if when and not when_valid:
            warnings.append(
                f"point when {when!r} is not a valid ISO date — dropped")
        about_list = [str(a) for a in (p.get("about_entities") or [])
                      if isinstance(a, str) and a.strip()]
        match, existing_id = _find_point_match(idx["points"], content,
                                               about_entities=about_list,
                                               when=when_valid)
        pid = _content_id("pt", content)
        if match == "exact":
            pid = existing_id or pid
            reason = "NEW"   # identical content → same content-addressed id → dedup
            link_before_create.append({
                "searched_for": f"point '{content[:60]}'", "found": True,
                "note": f"exact existing {pid} — content-addressed dedup"})
        elif match == "revises":
            reason = "REVISES"  # supersession: new content corrects the old
            link_before_create.append({
                "searched_for": f"point '{content[:60]}'", "found": True,
                "note": f"revises existing {existing_id} — supersede by correction"})
            # E5 (#1537): point-level supersession record riding the EXISTING
            # payload.supersessions field (pt_ prefix dispatch at the write
            # sites). Self-supersede guard — never fires for revises (new
            # content ⇒ new content-addressed id), kept for discipline.
            if existing_id and existing_id != pid:
                supersessions.append({
                    "superseded": existing_id, "supersedes_by": pid,
                    "evidence": "fact-value contradiction (later session "
                                "value change)"})
        else:
            reason = "NEW"
            link_before_create.append({
                "searched_for": f"point '{content[:60]}'", "found": False,
                "note": "no match — created"})
        point_ids[n] = pid
        # E3 (D1/D4): atomicity soft guard + E3 point keys. The verbatim
        # quote (<=200) is the anchor; search_keys are sanitized aliases;
        # source_turn_id is resolved DETERMINISTICALLY (the model's index
        # is advisory — a conflicting model index loses with a warning).
        sents = _split_sentences(content)
        if len(sents) > 1:
            warnings.append(f"point '{content[:60]}' has {len(sents)} sentences — "
                            "E3 atomicity expects ONE claim per point")
        quote = str(p.get("quote") or "").strip()[:200]
        search_keys = _clean_search_keys(p.get("search_keys"), warnings,
                                         f"point '{content[:60]}'")
        turn_idx = _resolve_source_turn(p, edus, warnings=warnings)
        pt_entry = {
            "id": pid, "content": content, "pointKind": pkind,
            "reason": reason, "confidence": 0.5, "c_cal": 0.5,
            "about_entities": about_list,
            "source_ref": "session.md", "quote": quote, "status": "draft",
            "search_keys": search_keys,
            "source_turn_id": turn_idx,
        }
        # E2 (#1534): Tier-A state-value marker — classification hint,
        # A-only emission (absence = Tier-B default → zero-diff payloads for
        # non-Tier-A sessions). Fail-loud guard: a Tier-A point without a
        # verbatim quote warns (never silent, never blocking).
        tier = str(p.get("tier") or "").strip().upper()
        if tier == "A":
            if not quote:
                warnings.append(
                    f"Tier-A point '{content[:60]}' has no verbatim quote — "
                    "the state value may not survive; quote required for Tier-A")
            pt_entry["tier"] = "A"
            tier_a_points += 1
        pt_slots = _clean_slots(p.get("slots"), warnings,
                                f"point '{content[:60]}'", master)
        pt_slots = _resolve_slot_refs(pt_slots, emitted_entity_keys, warnings,
                                      f"point '{content[:60]}'")
        if pt_slots:
            pt_entry["slots"] = pt_slots
        if when_valid:
            pt_entry["when"] = when_valid
        payload_points.append(pt_entry)

    # ── operators (dependency order 4) — TWO-PASS ─────────────────────────
    # Pass 1 emits IMPL/NAND and collects the emitted edges; pass 2 processes
    # MITIGATES against the COMPLETE edge set so order-independence holds
    # (a MITIGATES may legitimately precede its target IMPL in the model
    # output).
    def _resolve(ref: str) -> str:
        r = _norm(ref)
        if r in point_ids:
            return point_ids[r]
        if r in event_ids:
            return event_ids[r]
        return ""

    payload_operators: list[dict] = []
    emitted_edges: set[tuple[str, str, str]] = set()
    mitigates_pending: list[dict] = []
    for op in embed_list.get("operators", []) or []:
        if not isinstance(op, dict):
            warnings.append(f"non-dict operator entry {op!r} skipped")
            continue
        o = dict(op)
        op_type = str(o.get("op_type", "")).upper()
        if op_type not in ("IMPL", "NAND", "MITIGATES"):
            warnings.append(f"operator with unknown op_type {op_type!r} dropped")
            continue
        src = _resolve(str(o.get("src", "")))
        dst = _resolve(str(o.get("dst", "")))
        if not src or not dst or src == dst:
            warnings.append(f"operator dropped: src/dst did not resolve to an "
                            f"emitted point/event ({o.get('src')!r} → {o.get('dst')!r})")
            continue
        if op_type in ("IMPL", "NAND"):
            payload_operators.append({
                "src": src, "dst": dst, "op_type": op_type,
                "direction": "unidirectional"})
            emitted_edges.add((src, dst, op_type))
            continue
        # MITIGATES — relevance attack on an edge (deferred to pass 2)
        mitigates_pending.append({**o, "_src": src, "_dst": dst})

    for o in mitigates_pending:
        src, dst = o.pop("_src"), o.pop("_dst")
        target = o.get("target") or o.get("target_edge") or {}
        t_src = _resolve(str(target.get("src", "")))
        t_dst = _resolve(str(target.get("dst", "")))
        t_type = str(target.get("op_type", "IMPL")).upper()
        if t_type != "IMPL":
            t_type = "IMPL"
        try:
            strength = float(o.get("strength") or 0.3)
        except (TypeError, ValueError):
            warnings.append(f"MITIGATES strength {o.get('strength')!r} not numeric "
                            f"('{o.get('src', '')[:40]}'→'{o.get('dst', '')[:40]}') "
                            "→ defaulted to 0.3")
            strength = 0.3
        if not (0.10 <= strength <= 0.50):
            warnings.append(f"MITIGATES strength {strength} outside [0.10, 0.50] "
                            f"('{o.get('src', '')[:40]}'→'{o.get('dst', '')[:40]}') "
                            "clamped")
            strength = min(0.50, max(0.10, strength))
        if not (t_src and t_dst and (t_src, t_dst, "IMPL") in emitted_edges):
            warnings.append(f"MITIGATES target edge not emitted ({t_src!r}→{t_dst!r} "
                            "IMPL) — dropped")
            continue
        payload_operators.append({
            "src": src, "dst": dst, "op_type": "MITIGATES",
            "target": {"src": t_src, "dst": t_dst, "op_type": "IMPL"},
            "strength": round(strength, 2)})

    # ── chain validation (deterministic, warn+repair, never block) ───────
    chain_notes = validate_chains(embed_list, master)
    llm_chain_notes = [dict(c) for c in (embed_list.get("chain_notes") or [])
                       if isinstance(c, dict)]

    # ── minted-kind audit ─────────────────────────────────────────────────
    minted = _minted_kind_report(embed_list, master)

    # ── payload assembly (mirrors _summary_to_payload / _stream_to_payload) ─
    from datetime import datetime, timezone
    payload = {
        "schema_version": "1", "session_id": session_id,
        "client_commit_id": "",
        "captured_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "extractor": {"version": extractor_version, "mode": "byok",
                      "calibration_version": "v2"},
        "summary": (summary or "")[:2000],
        "story_arc": (story_arc or "")[:4000],
        "provenance_refs": [{"path": "session.md", "spans": []}],
        "sources": [],
        "entities": payload_entities, "points": payload_points,
        "events": payload_events, "operators": payload_operators,
        "supersessions": supersessions,
        "telemetry": {
            "extractor": {"version": extractor_version, "mode": "byok",
                          "calibration_version": "v2"},
            "model": {"provider": "byok", "id": "user-model", "cfg_hash": ""},
            "counts": {"kept": len(payload_points),
                       "candidate": len(payload_events),
                       "segment": 1, "window": 1, "empty_windows": 0},
            "keep_ratio": None, "dedup_hits": None, "frontier_calls": 1,
            "llm_cost_usd": None, "extraction_ms": 0, "retry_count": 0,
            "last_error_code": None, "confidence_histogram": None,
        },
    }
    # replay-safe client_commit_id (T5 #1272 pattern — complete on both paths).
    # E5 (#1537): supersessions are part of the canonical (#1350) — site 1
    # (execute_embed) must agree with site 2 (_post_commit) and site 3
    # (validate_layer1), or the hosted path 422s commit_id_mismatch on any
    # payload with non-empty supersessions. The canonical omits an EMPTY
    # list, so pre-#1350 payloads keep their id (additive contract).
    from tortoise.commit_schema import compute_client_commit_id
    payload["client_commit_id"] = compute_client_commit_id(
        payload["session_id"], payload["points"], payload["entities"],
        payload["operators"], payload["summary"], payload["story_arc"],
        payload.get("events", []), payload.get("supersessions", []))

    return {
        "payload": payload,
        "supersessions": supersessions,
        "chain_notes": llm_chain_notes + chain_notes,
        "link_before_create": link_before_create,
        "warnings": warnings,
        "minted_kinds": minted,
        "stats": {
            "entities": len(payload_entities), "events": len(payload_events),
            "points": len(payload_points), "operators": len(payload_operators),
            "tier_a_points": tier_a_points,
            "search_queries": (search or {}).get("queries_run", 0),
            "search_degraded": bool((search or {}).get("degraded")),
            "chain_notes": len(chain_notes),
            "supersessions": len(supersessions),
        },
    }


# ── The orchestrator ───────────────────────────────────────────────────────

def _edus_from_conversation(conversation: list[dict]) -> list[dict]:
    return [{"index": i, "role": str(t.get("role", "unknown")),
             "text": str(t.get("content", ""))}
            for i, t in enumerate(conversation) if t.get("content")]


def extract_session_v2(model, conversation: list[dict], *, sdk=None,
                       session_id: str | None = None, chunk_size: int = 50,
                       master: dict | None = None,
                       session_date: str | None = None) -> dict:
    """The v2 production entry: conversation → S1 (chunked+compiled) → S2 →
    S3 (real-backend search) → S4 (gap review) → S5 (embed execution).

    ``session_date`` (E1, #1533) is the ISO date/datetime the conversation
    happened on: it anchors the S1/S2/S4 prompts (DATE ANCHOR block) and
    drives S5's deterministic ``when``/``startedAt`` normalization. ``None`` /
    "" → date-blind: undated S1 prompts and all payloads are byte-identical
    to pre-E1 (S2/S4 prompts differ only via the unconditional OUTPUT_CONTRACT
    ``when``/``startedAt`` fields).

    Returns {"session_id", "story_arc", "embed_list", "search", "payload",
             "chain_notes", "link_before_create", "warnings", "minted_kinds",
             "stats", "errors"} — ``payload`` is the Layer-1 commit payload
    (client_commit_id complete; POST via the existing /v1/sessions/commit
    path).

    ``sdk`` is the graph client for S3 (optional — when absent or when the
    active backend is embedded, S3 degrades gracefully and the pipeline
    proceeds treating everything as new).
    """
    import uuid
    session_id = session_id or f"session_{uuid.uuid4().hex[:12]}"
    errors: list[str] = []
    # M3 (#1524, D3): per-session LLM roll-up + granular error census (class →
    # count across S1/S2/S4 failures). ``errors`` (strings) stays unchanged —
    # the additive keys feed the harness's per-question integrity (M4).
    llm_stats: dict = {"calls": 0, "retries": 0, "truncated": 0}
    error_census: dict[str, int] = {}
    edus = _edus_from_conversation(conversation)
    if not edus:
        return {"session_id": session_id, "story_arc": "", "embed_list": {},
                "search": {"mode": resolve_backend_mode(), "degraded": True,
                           "reason": "no conversation content"},
                "payload": None, "chain_notes": [], "link_before_create": [],
                "supersessions": [],
                "warnings": ["empty conversation — nothing extracted"],
                "minted_kinds": [], "stats": {"llm": llm_stats},
                "errors": errors, "error_census": error_census}

    t0 = time.time()
    # ── S1: chunked story summary + compile ────────────────────────────────
    # M3 (#1524, D3): each chunk's per-call stats (attempts/retries/truncated)
    # roll into ``llm_stats``; a chunk that exhausts retries (or hits a fatal
    # 4xx) bumps ``error_census`` with its class.
    chunks = chunk_transcript(edus, target=chunk_size)
    chunk_stories: list[str] = []
    failed_chunks = 0
    for chunk in chunks:
        stage_stats: dict = {}
        try:
            chunk_stories.append(run_s1(model, _edus_to_text(chunk),
                                        session_date=session_date,
                                        stats=stage_stats))
        except Exception as e:  # per-chunk failure is non-fatal
            failed_chunks += 1
            errors.append(f"S1 chunk failed: {type(e).__name__}: {e}")
            _bump_census(error_census, e)
        _rollup_llm(llm_stats, stage_stats)
    if failed_chunks:
        errors.append(f"{failed_chunks}/{len(chunks)} S1 chunks failed")
    story = compile_stories(chunk_stories)
    story_arc = story

    # ── S2: map to embed ───────────────────────────────────────────────────
    embed_list: dict = {}
    if story:
        stage_stats: dict = {}
        try:
            embed_list = run_s2(model, story, master,
                                session_date=session_date, edus=edus,
                                stats=stage_stats)
        except Exception as e:
            errors.append(f"S2 failed: {type(e).__name__}: {e}")
            _bump_census(error_census, e)
        _rollup_llm(llm_stats, stage_stats)

    # ── S3: search the graph (real backend, graceful degradation) ──────────
    search = search_graph(sdk, embed_list, story)

    # ── S4: review gaps → complete embed list ──────────────────────────────
    complete_list: dict = embed_list
    s4_warnings: list[str] = []
    if story:
        stage_stats: dict = {}
        try:
            s4 = run_s4(model, story, search, embed_list, master,
                        session_date=session_date, edus=edus,
                        stats=stage_stats)
            if s4 and (s4.get("entities") or s4.get("points") or
                       s4.get("events") or s4.get("operators")):
                complete_list = s4
            else:
                # graceful degradation — S2 output stands; not an error
                s4_warnings.append("S4 returned an empty list — kept S2 output")
        except Exception as e:
            errors.append(f"S4 failed: {type(e).__name__}: {e} — kept S2 output")
            _bump_census(error_census, e)
        _rollup_llm(llm_stats, stage_stats)

    # ── S5: embed execution (deterministic) ────────────────────────────────
    if not complete_list:
        errors.append("no embed list produced (S2/S4 empty) — nothing to embed")
    try:
        result = execute_embed(complete_list, search, session_id=session_id,
                               story_arc=story_arc, master=master,
                               session_date=session_date, edus=edus)
    except Exception as e:  # S5 must NEVER block the pipeline (design §7.4)
        errors.append(f"S5 failed: {type(e).__name__}: {e}")
        result = {"payload": None, "chain_notes": [], "link_before_create": [],
                  "supersessions": [],
                  "warnings": [f"S5 embed execution failed: {e}"],
                  "minted_kinds": [], "stats": {}}
    result["session_id"] = session_id
    result["story_arc"] = story_arc
    result["embed_list"] = complete_list
    result["s2_embed"] = embed_list          # S2 raw (pre-S4) — owner loop
    result["search"] = search
    result["errors"] = errors
    if s4_warnings:
        result["warnings"] = s4_warnings + (result.get("warnings") or [])
    result["stats"]["elapsed_s"] = round(time.time() - t0, 1)
    result["stats"]["chunks"] = len(chunks)
    result["stats"]["failed_chunks"] = failed_chunks
    # M3 (#1524, D3): additive integrity surface — the per-session census +
    # LLM roll-up feed the harness's per-question ``valid`` / ``error_classes``
    # (M4). The payload telemetry's hardcoded retry_count is wired to the
    # real value (previously always 0).
    result["error_census"] = error_census
    result["stats"]["llm"] = llm_stats
    if result.get("payload"):
        result["payload"]["telemetry"]["retry_count"] = llm_stats["retries"]
    return result


# ── Shared helpers ─────────────────────────────────────────────────────────

# ── M3 (#1524): retry/backoff + classification + bounded generations ──────
#
# Retry constants mirror the eval harness's ``_call_with_backoff``
# (tools/longmem_eval/run.py: BACKOFF_BASE_S=2.0 / BACKOFF_CAP_S=30.0) — one
# in-repo pattern, not a second invention (D1).

_COMPLETE_RETRIES = 2          # transient retries beyond the first (3 total)
_BACKOFF_BASE_S = 2.0
_BACKOFF_CAP_S = 30.0

# Bounded generations (D2): stage caps — S1 narrative is small (1500); the
# S2/S4 embed JSON must clear the 4000-token truncation floor (8000). The
# single env override TORTOISE_EXTRACTOR_MAX_TOKENS (int, read at call time)
# raises BOTH stages without a code change — the retry-then-fix protocol's
# mechanical lever (D6: ``transient_timeout`` spike → raise the cap).
_S1_MAX_TOKENS = 1500
_S2_S4_MAX_TOKENS = 8000


def _stage_cap(default: int) -> int:
    """Stage token cap: TORTOISE_EXTRACTOR_MAX_TOKENS env override (read at
    call time — the mechanical-fix loop raises caps without a code change) or
    the stage default. An unparseable override warns loudly and uses the
    default (fail-open with visibility, never a silent no-op)."""
    raw = os.environ.get("TORTOISE_EXTRACTOR_MAX_TOKENS", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            warnings.warn(
                f"invalid TORTOISE_EXTRACTOR_MAX_TOKENS={raw!r} — using "
                f"stage default {default}", stacklevel=2)
    return default


_CAP_KIND_CACHE: weakref.WeakKeyDictionary[Any, str] = \
    weakref.WeakKeyDictionary()


def _cap_kind(model) -> str:
    """How this model instance accepts a token cap (D2): 'kwarg' (the
    ``complete()`` signature accepts ``max_tokens`` — the preferred, race-free
    path), 'attr' (writable ``max_tokens`` attribute — the documented degraded
    path), or 'unbounded' (neither — warned + recorded, fail-open with
    visibility).

    Cached per model INSTANCE (WeakKeyDictionary — the eval shares ONE
    extractor model across worker threads (``--workers>1``) and per-call
    kwargs are race-free; the attr fallback mutates shared state and is only
    safe for a single-model-per-run, GATE-2)."""
    try:
        return _CAP_KIND_CACHE[model]
    except (KeyError, TypeError):  # un-weakref-able → compute each call
        pass
    try:
        sig = inspect.signature(model.complete)
        kind = ("kwarg" if "max_tokens" in sig.parameters
                else ("attr" if hasattr(model, "max_tokens")
                      else "unbounded"))
    except (TypeError, ValueError):  # signature unavailable
        kind = "attr" if hasattr(model, "max_tokens") else "unbounded"
    with contextlib.suppress(TypeError):
        _CAP_KIND_CACHE[model] = kind
    return kind


def _cap_kwargs(model, max_tokens: int | None, stats: dict | None) -> dict:
    """Per-call kwargs that enforce ``max_tokens`` on this adapter (D2).

    Preferred: the per-call ``max_tokens`` kwarg (race-free under workers).
    Fallback: set the writable ``max_tokens`` attribute before the call.
    Neither → loud warning + ``stats['unbounded_adapter']`` (never a silent
    no-op). ``max_tokens=0`` is the documented UNCAPPED escape hatch (no
    kwarg, no mutation)."""
    if not max_tokens:
        return {}
    kind = _cap_kind(model)
    if kind == "kwarg":
        return {"max_tokens": max_tokens}
    if kind == "attr":
        model.max_tokens = max_tokens
        return {}
    warnings.warn(
        f"model {type(model).__name__} does not accept a max_tokens kwarg "
        f"and has no writable max_tokens attribute — generation is UNBOUNDED "
        f"for this call (recorded in stats['unbounded_adapter']); add the "
        f"kwarg to the adapter (M3 #1524 GATE-2)", stacklevel=3)
    if stats is not None:
        stats["unbounded_adapter"] = True
    return {}


def _classify_error(e: BaseException) -> str:
    """Granular census class for one LLM-call exception (D3 vocabulary).

    Stable keys (the report census + retry-then-fix triage read these):
    fatal_401_auth / fatal_402_billing / fatal_403_forbidden / fatal_4xx /
    transient_429_rate_limit / transient_5xx / transient_timeout /
    transient_network / transient_unknown. ``parse_error`` and ``truncated``
    are produced by the stage callers, not here.

    Duck-typed (``e.response.status_code``) so the extractor stays free of a
    hard ``requests`` import — semantically identical to P2's taxonomy table
    (#1530: 401/402/403 fatal, 429/5xx transient, other 4xx fatal)."""
    st = getattr(getattr(e, "response", None), "status_code", None)
    if st is not None:
        if st == 429:
            return "transient_429_rate_limit"
        if 500 <= st < 600:
            return "transient_5xx"
        if st == 401:
            return "fatal_401_auth"
        if st == 402:
            return "fatal_402_billing"
        if st == 403:
            return "fatal_403_forbidden"
        if 400 <= st < 500:
            return "fatal_4xx"
        return "transient_unknown"
    name = type(e).__name__
    if isinstance(e, TimeoutError) or "Timeout" in name:
        return "transient_timeout"
    if "Connection" in name or "Network" in name:
        return "transient_network"
    return "transient_unknown"


def _is_fatal_error(e: BaseException) -> bool:
    """True → the retry loop raises immediately (zero retries).

    Consumes P2's taxonomy export (``tortoise.model_adapters.is_fatal`` —
    401/402/403 FATAL + 400/404/other-4xx FATAL_CONFIG are permanent; never
    retried, MECE fix #1524). The local ``_classify_error`` fallback mirrors
    the same semantics (the ``fatal_*`` census prefix) so the retry decision
    can never diverge from the census classes (GATE-1: one taxonomy)."""
    try:
        from tortoise.model_adapters import is_fatal
        return is_fatal(e)
    except ImportError:  # pragma: no cover — P2 landed; defensive fallback
        return _classify_error(e).startswith("fatal")


class _ParseError(ValueError):
    """S2/S4 output that fails ``_parse_json`` — census class ``parse_error``
    (a prompt/OUTPUT_CONTRACT regression, not a provider condition; the D6
    triage treats a ``parse_error`` spike as a fix-the-prompt, not a
    retry-harder)."""


def _bump_census(error_census: dict[str, int], e: BaseException) -> None:
    """Record one stage-failure in the per-session census (D3)."""
    cls = "parse_error" if isinstance(e, _ParseError) else _classify_error(e)
    error_census[cls] = error_census.get(cls, 0) + 1


def _rollup_llm(llm_stats: dict, stage_stats: dict) -> None:
    """Roll one stage's per-call stats into the per-session LLM roll-up
    (D3: stats['llm'] = calls / retries / truncated across S1/S2/S4)."""
    llm_stats["calls"] += stage_stats.get("attempts", 0)
    llm_stats["retries"] += stage_stats.get("retries", 0)
    llm_stats["truncated"] += int(bool(stage_stats.get("truncated")))


def _call_once(model, system: str, user: str, *, deadline_s: int,
               max_tokens: int | None, stats: dict | None) -> str:
    """One wall-clock-bounded completion attempt (M3 D1: each retry attempt
    gets its OWN deadline — a wedged call cannot stay wedged across retries).

    The model call runs in a thread; exceptions are captured and RE-RAISED
    after join (Python threads do not propagate exceptions to the joiner —
    without this, a rate-limit/5xx would silently return None and the caller
    would record a phantom empty chunk)."""
    import threading
    box: dict = {}

    def _run():
        try:
            kwargs = _cap_kwargs(model, max_tokens, stats)
            box["resp"] = model.complete(system=system, user=user, **kwargs)
        except BaseException as e:  # noqa: BLE001, RUF100
            box["exc"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=deadline_s)
    if t.is_alive():
        raise TimeoutError(f"model call exceeded {deadline_s}s")
    if "exc" in box:
        raise box["exc"]
    return box.get("resp")


def _complete(model, system: str, user: str, *, deadline_s: int = 600,
              max_tokens: int | None = None, retries: int = _COMPLETE_RETRIES,
              backoff_base: float = _BACKOFF_BASE_S,
              backoff_cap: float = _BACKOFF_CAP_S,
              stats: dict | None = None) -> str:
    """Wall-clock-bounded completion with retry/backoff (M3 #1524, D1).

    Transient classes (429/5xx/network/TimeoutError — incl. the per-attempt
    deadline) are retried with exponential backoff (base 2.0, cap 30.0,
    jittered ×[0.5, 1.0)) up to ``retries`` (default 2 → 3 attempts); the
    last exception propagates. Fatal 4xx (401/402/403 + other 4xx, via P2's
    taxonomy) raise immediately with ZERO retries; unknown classes are
    treated as transient (the census records them — the retry-then-fix loop
    tightens the class, not the code).

    ``max_tokens`` bounds the generation (None = caller-applied stage cap;
    0 = documented uncapped escape hatch). ``stats`` (optional) records
    attempts / retries / truncated / last_class per call for the per-session
    LLM roll-up (D3).

    Total worst-case wall clock = attempts × deadline_s (documented — the
    abandoned daemon thread after a deadline keeps running and the provider
    keeps billing; accepted, bounded per attempt)."""
    for attempt in range(1, retries + 2):
        try:
            resp = _call_once(model, system, user, deadline_s=deadline_s,
                              max_tokens=max_tokens, stats=stats)
            truncated = getattr(model, "last_finish_reason", None) == "length"
            if stats is not None:
                stats.update(attempts=attempt, retries=attempt - 1,
                             last_class=None, truncated=bool(truncated))
            return resp
        except BaseException as e:  # noqa: BLE001, RUF100
            if not isinstance(e, Exception):  # never retry SystemExit/KeyboardInterrupt
                raise
            if stats is not None:
                stats.update(attempts=attempt, retries=attempt - 1,
                             last_class=_classify_error(e))
            if _is_fatal_error(e) or attempt > retries:
                raise
            wait = min(backoff_base ** attempt, backoff_cap) \
                * (0.5 + random.random() / 2)
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def _parse_json(response: str) -> dict:
    m = re.search(r"\{.*\}", response or "", re.S)
    if not m:
        raise ValueError("no JSON block in output")
    block = m.group(0)
    for cut in (None, -1, -2, -3, -5, -10, -20):
        try:
            return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError:
            continue
    raise ValueError("unparseable JSON")


__all__ = [  # noqa: RUF022
    "build_master_list", "master_kind_forms",
    "run_s1", "chunk_transcript", "compile_stories",
    "run_s2", "render_s2_prompt",
    "resolve_backend_mode", "search_graph",
    "run_s4", "render_s4_prompt",
    "execute_embed", "validate_chains", "derive_supersessions",
    "extract_session_v2",
    "S1_TMPL", "S2_TMPL", "S4_TMPL", "OUTPUT_CONTRACT",
    "SUBJECTS", "EVENTS", "CHAINS",
]
