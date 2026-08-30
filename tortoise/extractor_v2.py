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
import threading
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

PACK_NS = ("product-strategy:", "dev:", "marketing:", "pm:", "agent-ops:")

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


def _build_master_from_brief(brief: dict,
                             pack_prefixes: tuple[str, ...] = PACK_NS) -> dict:
    """The master-list sections from a compiled value brief (#2031 refactor
    of the build_master_list loop body — the section semantics are
    byte-identical to pre-#2031). ``pack_prefixes`` is the namespace
    allowlist for the pack_kinds section: the DEFAULT path passes the
    starter set; the hosted tenant path passes starter + that tenant's
    namespaces. Loop semantics preserved exactly: the memory_granularity
    skip precedes the prefix check, and pack_kinds keeps the brief's
    insertion order (prompt-visible)."""
    objects = {k: _desc(brief, k) for k in CORE_OBJECT_KEYS}
    pack_kinds = {}
    for k, v in brief.items():  # noqa: B007
        if k == "memory_granularity":
            continue
        if not k.startswith(pack_prefixes):
            continue
        pack_kinds[k] = _desc(brief, k)
    return {
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


def build_master_list(sdk=None) -> dict:
    """The v2 master list: compile_value_brief() kinds + the §3 additions
    (subjects, points, events, chains, memory_granularity). Section values
    are {kind: description} dicts — rendered as readable text in prompts.

    Memoized (#1350 chunking finding): the packs are static per process —
    re-reading + YAML-parsing every manifest per chunk cost 12.2s of a 14.3s
    60-chunk run. Returns a deep copy so callers can't mutate the cache.

    #2031 hosted tenant path (``sdk``): the master compiles from the
    memoized tenant view's brief (shared catalog + THIS tenant's
    :PackManifest manifests) with the pack_kinds allowlist extended to the
    tenant's namespaces — so tenant A's pack kinds reach A's extraction
    prompts and write gates while tenant B's never do. The tenant identity
    is the SDK's resolved graph (pass the tenant-scoped SDK,
    ``_make_sdk(namespace=team_id)`` — no separate identity argument to
    mismatch). The tenant path NEVER reads or writes the process-global
    ``_MASTER_LIST_CACHE`` (#1154: a tenant-scoped compile must not poison
    the shared memo); the #1350 perf guard rides the tenant-view memo (per
    (graph_identity, pack_config_version), invalidated on :PackManifest
    write) instead.
    """
    if sdk is None:
        import copy
        global _MASTER_LIST_CACHE
        if _MASTER_LIST_CACHE is not None:
            return copy.deepcopy(_MASTER_LIST_CACHE)
        from tortoise.value_extractor import compile_value_brief
        brief = compile_value_brief()
        master = _build_master_from_brief(brief, PACK_NS)
        _MASTER_LIST_CACHE = copy.deepcopy(master)
        return master
    from tortoise.pack_manifest_store import tenant_view
    view = tenant_view(sdk)
    tenant_prefixes = tuple(f"{m['namespace']}:" for m in view["tenant"])
    return _build_master_from_brief(view["brief"], PACK_NS + tenant_prefixes)


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


# ── Compact master render (pilot #1549 prompt-efficiency research) ────────
# Toggle: TORTOISE_EXTRACTOR_PROMPT=compact. SAME vocabulary (the real
# ontology + packs) — tighter presentation: kind names only (gloss retained
# only when it disambiguates), selective pack injection by story keywords,
# and NO chains section here (chains_text is the single source — dedup).
# Research: 4,700-token S2 prompt → ~1,900-2,300 (~2-2.5x); evidence says
# shorter context IMPROVES structured-output quality (lost-in-the-middle),
# and "Mind Your Format" says A/B on the actual model — hence the toggle.

# Pack namespace → trigger words (conservative: match → inject the section).
_PACK_TRIGGERS = {
    "dev:": ("dev", "epic", "issue", "code", "deploy", "sprint", "pr ",
             "pull request", "bug", "commit", "test", "repo"),
    "marketing:": ("marketing", "campaign", "audience", "content", "post",
                   "channel", "social"),
    "product-strategy:": ("product", "market", "competitor", "customer",
                          "roadmap", "feature", "use case", "strategy"),
    "pm:": ("project", "milestone", "pm:", "portfolio", "program"),
    "agent-ops:": ("standard operating", "protocol", "token acknowledgement",
                    "destructive action", "policy", "standing rule"),
    # NOTE (#2031): the legacy "epistemic-team:" entry was removed — it
    # referenced a pack that is not installed, so it could never fire on the
    # default path; its presence would have trigger-gated a TENANT pack named
    # `epistemic-team` (not a reserved namespace), silently stripping that
    # tenant's own kinds from compact-mode prompts.
}


def _needs_gloss(kind: str, desc: str) -> bool:
    """Gloss only when it disambiguates (research: names-only for the rest)."""
    d = desc.strip()
    if not d:
        return False
    low = d.lower()
    if any(m in low for m in ("not", "uncertain", "≡", "link:", "never", "only")):
        return True
    if len(d.split()) > 6:  # longer descriptions carry real semantics  # noqa: SIM103
        return True
    return False


def _select_pack_kinds(story: str | None, pack_kinds: dict) -> dict:
    """Selective pack injection: only sections whose domain words appear in
    the story. Conservative fallback — if nothing matches, return all so no
    needed kind is ever dropped (the flag-on path never calls this — the
    pack vocabulary is owned by ``_render_master_core_only``)."""
    if not story:
        return dict(pack_kinds)
    low = story.lower()
    selected = {}
    for k, v in pack_kinds.items():
        ns = k.split(":")[0] + ":"
        triggers = _PACK_TRIGGERS.get(ns)
        # #2031: a namespace with NO trigger entry cannot be story-selected —
        # always include it (per-tenant custom packs; dropping them would
        # silently strip the tenant's own kinds from their compact prompt).
        # All five starter namespaces have trigger entries, so the DEFAULT
        # path behavior is unchanged (byte-identical).
        if triggers is None or any(t in low for t in triggers):
            selected[k] = v
    return selected or dict(pack_kinds)  # nothing matched → all (safe)


def _stable_hash(s: str) -> int:
    """Deterministic 64-bit hash for the A′ label-order seed (Task 2,
    #1695): same story → same seed → same shuffle order across paired
    re-runs (the A′ diagnostic compares bit-level agreement)."""
    import hashlib
    return int(hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:16], 16)


def _label_order_rng(story: str | None = None):
    """A′ (#1695 Task 2): the kind-list shuffle RNG — the label-order
    randomization mitigation (Fantastically Ordered Prompts) and the A′
    diagnostic hook. OFF by default (``TORTOISE_LABEL_ORDER`` unset →
    None → byte-identical renders). ``TORTOISE_LABEL_ORDER=shuffle`` enables
    a deterministic per-call seeded shuffle: the seed is the env override
    ``TORTOISE_LABEL_ORDER_SEED`` (int) or a hash of the story (same story
    → same order, so a paired fresh canonical re-run compares the SAME
    sessions under a different order)."""
    import os
    import random
    if os.environ.get("TORTOISE_LABEL_ORDER", "").strip().lower() != "shuffle":
        return None
    seed_raw = os.environ.get("TORTOISE_LABEL_ORDER_SEED", "").strip()
    if seed_raw:
        try:
            return random.Random(int(seed_raw))
        except ValueError:
            import warnings
            warnings.warn(
                f"invalid TORTOISE_LABEL_ORDER_SEED={seed_raw!r} — falling "
                "back to the story-derived seed", stacklevel=2)
    return random.Random(_stable_hash(story))


def _classify_later_enabled() -> bool:
    """#1695 Task 5: the call-time classify-later toggle — read at the
    single choke point (extract_session_v2 + the render dispatchers) so ALL
    callers (sdk, hosted_api, ingest_v2, run_v2_pipeline) inherit it without
    threading a param. Unset/0 → the legacy pipeline: the classify-later
    machinery is entirely off-path and the flag-off renders are
    byte-identical to main — scoped to the DEFAULT (verbose) render (the
    compact-mode S2 story-threading is pre-existing and identical to main;
    the chain enforcer runs unconditionally on every arm and its result key
    reflects that run; only the additive ``classify_later`` result key is
    an empty block when the flag is off — see ``extract_session_v2``).
    Value matching is case-insensitive (True/TRUE/ON/yes all enable —
    review FIX B)."""
    import os
    return os.environ.get("TORTOISE_CLASSIFY_LATER", "").strip().lower() in (
        "1", "true", "yes", "on")


def _default_kind_classifier(model):
    """The default classify-later classifier (built lazily — index build is
    the first-use cost; the EmbeddingModel singleton is shared, never
    re-instantiated). The session's LLM adapter powers the adjudication
    tail."""
    from tortoise.kind_classifier import KindClassifier
    return KindClassifier(model=model)


def _render_master(master: dict, story: str | None = None, *,
                   core_only: bool | None = None) -> str:
    import os
    if core_only is None:
        core_only = _classify_later_enabled()
    rng = _label_order_rng(story)
    if core_only:
        return _render_master_core_only(master, rng)
    if os.environ.get("TORTOISE_EXTRACTOR_PROMPT", "").strip() == "compact":
        return _render_master_compact(master, story, rng)
    return _render_master_verbose(master, rng)


def _render_master_core_only(master: dict, rng=None) -> str:
    """#1695 Task 5: the flag-on S2/S4 master render — the verbose base
    MINUS the PACK KINDS and CHAINS sections (the pack vocabulary and the
    chain business logic leave the prompt; the kind classifier and the
    chain enforcer own them post-extraction). The user-personal-state
    vocabulary, memory-granularity, and the state-value carve-out are
    RETAINED (the verbose base). Kind groups randomize under the A′ hook."""
    lines = [
        "MASTER LIST — the closed vocabulary (CORE ONLY). EVERY kind you "
        "emit MUST come from this list (namespaced or bare form) or be "
        "\"unclassified\" (pack-domain content is typed by a later stage). "
        "Do NOT mint kinds: \"worktree\", \"test suite\", \"approach\" are "
        "NOT kinds — re-map to the nearest listed kind or drop the item.",
    ]

    def _group(title: str, d: dict, shuffle: bool = False) -> str:
        keys = list(d)
        if shuffle and rng is not None:
            rng.shuffle(keys)
        out = [f"\n{title}"]
        out += [f"- {k} — {d[k]}" for k in keys]
        return "\n".join(out)

    lines.append(_group("OBJECTS (core)", master["objects"], shuffle=True))
    lines.append(_group("SUBJECTS (core)", master["subjects"], shuffle=True))
    lines.append(_group("POINTS", master["points"], shuffle=True))
    lines.append(_group("EVENTS", master["events"], shuffle=True))
    # NO PACK KINDS and NO CHAINS — the classify-later/chain-enforcement
    # layers own them (prompt shrinks ~2x; label-space research).
    ups = master.get("user_personal_state") or {}
    if ups:
        lines.append("\nUSER-PERSONAL-STATE VOCABULARY (Tier-A classification "
                     "hint — the VALUE is the fact, retain verbatim; these are "
                     "NOT kinds: do NOT emit them as entity/event/point kinds)")
        lines += [f"- {cat}: {desc}" for cat, desc in ups.items()]
    g = master.get("memory_granularity") or {}
    if g:
        lines.append("\nMEMORY GRANULARITY (what to keep, what to strip)")
        lines += [f"- {k}: {v}" for k, v in g.items()]
    lines.append("\n" + STATE_VALUE_CARVE_OUT)
    return "\n".join(lines)


def _render_master_compact(master: dict, story: str | None,
                           rng=None) -> str:
    lines = [
        "MASTER LIST — the closed vocabulary. EVERY kind you emit MUST come "
        "from this list. Do NOT mint kinds.",
    ]

    def _group(title: str, d: dict, shuffle: bool = False) -> str:
        keys = list(d)
        if shuffle and rng is not None:
            rng.shuffle(keys)
        out = [f"\n{title}"]
        for k in keys:
            v = d[k]
            out.append(f"- {k}" if not _needs_gloss(k, v) else f"- {k} — {v}")
        return "\n".join(out)

    lines.append(_group("OBJECTS (core)", master["objects"], shuffle=True))
    lines.append(_group("SUBJECTS (core)", master["subjects"], shuffle=True))
    lines.append(_group("POINTS", master["points"], shuffle=True))
    lines.append(_group("EVENTS", master["events"], shuffle=True))
    selected = _select_pack_kinds(story, master["pack_kinds"])
    lines.append(_group("PACK KINDS", selected, shuffle=True))
    # granularity: matched-domain subsections only (compact). Hint blocks
    # (memory granularity, user-personal-state, carve-out) are EXCLUDED from
    # the shuffle — only the kind vocabulary randomizes.
    g = master.get("memory_granularity") or {}
    if story:
        low = story.lower()
        sub = {k: v for k, v in g.items()
               if any(t in low for t in _PACK_TRIGGERS.get("dev:", ()) + _PACK_TRIGGERS.get("product-strategy:", ()))}
        if sub:
            lines.append(_group("MEMORY GRANULARITY", sub))
    # NO chains section — chains_text renders it once (dedup).
    return "\n".join(lines)


def _render_master_verbose(master: dict, rng=None) -> str:
    lines = [
        "MASTER LIST — the closed vocabulary. EVERY kind you emit MUST come "
        "from this list (namespaced or bare form). Do NOT mint kinds: "
        "\"worktree\", \"test suite\", \"approach\" are NOT kinds — re-map "
        "to the nearest listed kind or drop the item.",
    ]

    def _group(title: str, d: dict, shuffle: bool = False) -> str:
        keys = list(d)
        if shuffle and rng is not None:
            rng.shuffle(keys)
        out = [f"\n{title}"]
        out += [f"- {k} — {d[k]}" for k in keys]
        return "\n".join(out)

    lines.append(_group("OBJECTS (core)", master["objects"], shuffle=True))
    lines.append(_group("SUBJECTS (core)", master["subjects"], shuffle=True))
    lines.append(_group("POINTS", master["points"], shuffle=True))
    lines.append(_group("EVENTS", master["events"], shuffle=True))
    lines.append(_group("PACK KINDS (from the installed packs)",
                        master["pack_kinds"], shuffle=True))

    lines.append("\nCHAINS (the business logic of mapping)")
    for name, c in master["chains"].items():
        path = " → ".join(str(x) for x in c.get("path", []))
        note = c.get("note", "")
        lines.append(f"- {name}: {path}" + (f" — {note}" if note else ""))

    ups = master.get("user_personal_state") or {}
    if ups:
        lines.append("\nUSER-PERSONAL-STATE VOCABULARY (Tier-A classification "
                     "hint — the VALUE is the fact, retain verbatim)")
        lines += [f"- {cat}: {desc}" for cat, desc in ups.items()]

    g = master.get("memory_granularity") or {}
    if g:
        lines.append("\nMEMORY GRANULARITY (what to keep, what to strip)")
        lines += [f"- {k}: {v}" for k, v in g.items()]
    lines.append("\n" + STATE_VALUE_CARVE_OUT)
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


def _granularity_text(master: dict | None = None) -> str:
    """The S1 memory-granularity slot. E2 (D3): appends the STATE-VALUE
    CARVE-OUT so S1's granularity rules protect user-personal-state values
    from the mechanics-token filter — S1's "RESTATE, DON'T REINVENT" rule
    then carries the value verbatim into the story.

    #2031: ``master`` is the tenant-scoped master on the hosted path (so a
    tenant pack's memory_granularity reaches the S1 prompt); None → the
    default master (byte-identical)."""
    master = master or build_master_list()
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
           stats: dict | None = None,
           master: dict | None = None) -> str:
    """S1: story summary for ONE segment. Returns the narrative text
    (the validated single-flash path). Generation is bounded at
    ``_S1_MAX_TOKENS`` (M3 #1524, D2 — capped output, truncation detected
    via ``last_finish_reason == "length"``, never silently lost).

    #2031: ``master`` threads the tenant-scoped vocabulary to the S1
    granularity slot on the hosted path (None → default master)."""
    system = (S1_TMPL
              .replace("{memory_granularity}", _granularity_text(master))
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
  "link_before_create": [{"searched_for": str, "found": bool, "note": str}],
  "retractions": [{"content": str}|"id": str]  # E7: explicit withdrawals — emit when the conversation RETRACTS a previously-stated fact (additive; omit when nothing is withdrawn)
}"""


# #1695 Task 5: the flag-on OUTPUT_CONTRACT — the base contract plus the
# ``unclassified`` sentinel (pack-domain content is typed by the classify-
# later stage). Kept as a SEPARATE constant so the flag-off contract stays
# byte-identical (the sentinel is only legal when the pack vocabulary is
# out of the prompt).
OUTPUT_CONTRACT_CORE_ONLY = (
    OUTPUT_CONTRACT
    # The full entities anchor — the three PARTICIPANT-SLOT kind fields
    # ("name": str, "kind": str, "confidence") share the bare
    # '"kind": str,' fragment and must NOT advertise the sentinel (the
    # write path would silently undo it). Only the top-level fields widen.
    .replace('"name": str, "kind": str, "lifecycle"',
             '"name": str, "kind": str|"unclassified", "lifecycle"')
    .replace('"eventKind": str,', '"eventKind": str|"unclassified",')
    .replace('"pointKind": "statement",',
             '"pointKind": "statement"|"unclassified",')
)



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


# #1695 Task 5: the flag-on S2 template — the base template with the pack
# vocabulary/chain-reasoning removed and the emit-untyped instruction added
# (pack-domain content → "unclassified", typed by the classify-later stage).
# Derived so the flag-off S2_TMPL stays byte-identical.
S2_TMPL_CORE_ONLY = S2_TMPL.replace(
    "- CHAINS — mapping must respect the chain positions (WARN, then TRY TO REPAIR):\n"
    "{chains_text}\n"
    "  If a mapping would connect across a chain in a way that violates it, WARN in\n"
    "  chain_notes and TRY TO REPAIR by re-mapping toward the nearest valid chain\n"
    "  position. NEVER invent entities to satisfy a chain.",
    "CHAIN ENFORCEMENT IS DETERMINISTIC: pack-chain positions are enforced by a\n"
    "post-extraction graph pass (the chain_notes field stays for your flags only).\n"
    "Do NOT reason about chain paths here — emit the untyped items and the\n"
    "enforcer rewires them. NEVER invent entities to satisfy a chain.",
).replace(
    "MASTER LIST\n{master_list}\n\nCONDENSED SEMANTIC CORE",
    "MASTER LIST (CORE ONLY — the pack vocabulary is NOT here)\n{master_list}\n\n"
    "PACK-DOMAIN CONTENT → UNCLASSIFIED: content whose kind would be a PACK kind\n"
    "({pack_namespaces}) is NOT in the\n"
    "vocabulary above. For such content emit kind/eventKind/pointKind: \"unclassified\"\n"
    "— a later deterministic stage assigns the pack kind. Core kinds are in-context:\n"
    "emit them directly. NEVER mint a pack kind name you cannot see in the list.\n\n"
    "CONDENSED SEMANTIC CORE",
)


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
                     edus: list[dict] | None = None,
                     story: str | None = None,
                     core_only: bool | None = None) -> str:
    """The S2 prompt. ``core_only`` (None = env fallback) selects the
    #1695 Task 5 core-only variant (pack vocabulary + chains out of the
    prompt, the "unclassified" sentinel in the contract); the flag-off
    default renders byte-identically to today."""
    master = master or build_master_list()
    if core_only is None:
        core_only = _classify_later_enabled()
    transcript = _render_source_transcript(edus)
    tmpl = S2_TMPL_CORE_ONLY if core_only else S2_TMPL
    contract = OUTPUT_CONTRACT_CORE_ONLY if core_only else OUTPUT_CONTRACT
    chains = "" if core_only else _render_chains(master)
    # The pack-namespace list is DYNAMIC — derived from the INSTALLED packs
    # (master["pack_kinds"] keys), never hardcoded (epistemic-team is not
    # installed; a future pack must route to unclassified too).
    pack_ns = "/".join(sorted(
        {k.rsplit(":", 1)[0] + ":" for k in (master.get("pack_kinds") or {})}
    )) if core_only else ""
    return (tmpl
            .replace("{master_list}", _render_master(
                master, story, core_only=core_only))
            .replace("{pack_namespaces}", pack_ns)
            .replace("{chains_text}", chains)
            .replace("{date_anchor}", _date_anchor(
                session_date, include_emission_rules=True))
            .replace("{output_contract}", contract)
            + (("\n\n" + transcript) if transcript else ""))


_PARSE_RETRIES = 1  # re-prompt once on unparseable S2/S4 output (pilot #1549 fix:
# parse_error dominated the pilot census 18-31/qid — LLM sloppiness, self-corrects)


def _error_excerpt(response: str, err: BaseException) -> str:
    """Bounded error region for the error-informed re-prompt (D3, #1746):
    the region around the JSONDecodeError position (±150 chars) when the
    error exposes one, else the last 400 chars of the response; the excerpt
    is bounded at 500 chars total. ``None`` responses are tolerated
    (``_parse_json`` already treats them as empty)."""
    resp = response or ""
    pos = getattr(err, "pos", None)
    if isinstance(pos, int) and 0 <= pos < len(resp):
        excerpt = resp[max(0, pos - 150):pos + 150]
    else:
        excerpt = resp[-400:]
    return excerpt[:500]


def _complete_parsed(model, system: str, user: str, *,
                     max_tokens: int | None, stats: dict | None) -> dict:
    """``_complete`` + the parse-recovery ladder with one error-informed
    re-prompt on parse failure (pilot #1549 + #1746 D3/D4).

    Transient/fatal classification lives in the ``_complete`` retry loop
    (M3); this layer adds the parse-retry the pilot census demanded: a
    parse_error is usually LLM sloppiness and the model self-corrects on
    an ERROR-INFORMED re-prompt. Retries are counted in
    ``stats["llm"]["retries"]`` (D3) and the census records the final
    failure as ``parse_error`` / ``truncated_parse_error`` (#1746 D2: the
    FIRST parse-failing attempt's ``finish_reason`` decides the class —
    ``length`` → the truncation hypothesis, else sloppiness/contamination).

    Retry policy (#1746 D3): a first parse-failing attempt with
    ``finish_reason == "length"`` SKIPS the retry — same prompt + same cap
    is deterministic failure (the ladder's rung-4 partial-accept already
    ran in-process on attempt 1); a ``stop``/``None`` failure re-prompts
    with the bounded parse-error block (``_error_informed_reprompt``)."""
    attempts = 0
    last: Exception | None = None
    first_truncated = False
    # D7 (#1746): the stage-level truncated flag ORs over ALL calls of the
    # stage (``_complete`` overwrites ``stats["truncated"]`` per attempt) —
    # a truncated first attempt whose retry recovers must still record the
    # truncation (criterion 3: no UNRECORDED truncation with valid=true).
    stage_truncated = False
    user_msg = user
    while attempts <= _PARSE_RETRIES:
        attempts += 1
        response = _complete(model, system, user_msg,
                             max_tokens=max_tokens, stats=stats)
        # D2 (#1746) + F4 (#1780): the finish reason is captured race-free
        # in the calling thread by ``_complete`` and recorded into stats —
        # reading the shared adapter attribute here would be a cross-thread
        # race under ``--workers > 1`` (extract_session_v2 always passes
        # stage_stats). The no-stats public-API path (run_s2/run_s4 default)
        # falls back to the adapter attribute — preserving the pre-F4 D3
        # retry-skip for truncated responses. Hold the FIRST parse-failing
        # attempt's value — the class-decision signal for
        # ``_ParseError.truncated``.
        finish = stats.get("finish_reason") if stats is not None else (
            getattr(model, "last_finish_reason", None))
        stage_truncated = stage_truncated or finish == "length"
        if stats is not None:
            stats["truncated"] = stage_truncated
        try:
            return _parse_json_robust(response, stats=stats)
        except ValueError as e:
            # _ParseError IS a ValueError subclass — the only exception the
            # ladder raises; ``_complete`` sits OUTSIDE this try, so a raw
            # adapter ValueError propagates to the stage except and is
            # classed by ``_classify_error`` (no parse-retry).
            last = e
            if attempts == 1:
                # D2 (#1746): the FIRST parse-failing attempt's finish
                # reason decides the class (a raw adapter ValueError has no
                # truncated attr — getattr-safe for the final raise).
                first_truncated = finish == "length"
            if finish == "length":
                # D3 (#1746): deterministic failure — same prompt + same cap
                # re-fails identically; the ladder's partial-accept already
                # recovered what it could in-process.
                break
            if attempts <= _PARSE_RETRIES:
                if stats is not None:
                    stats.setdefault("llm", {}).setdefault("retries", 0)
                    stats["llm"]["retries"] += 1
                user_msg = _error_informed_reprompt(user, response, e)
    raise _ParseError(str(last), truncated=first_truncated,
                      attempt=attempts,
                      excerpt=(getattr(last, "excerpt", None)
                               or _error_excerpt(response, last))) from last


def _error_informed_reprompt(user: str, response: str,
                             err: _ParseError) -> str:
    """D3 (#1746): the attempt-2 user message — the original prompt plus the
    bounded parse-error block (message ≤ 300 chars + excerpt ≤ 500)."""
    msg = str(err.args[0] if err.args else err)[:300]
    excerpt = getattr(err, "excerpt", None) or _error_excerpt(response, err)
    return (user + "\n\nYour previous response did not parse as the required "
            "JSON.\nParse error: " + msg +
            "\nOffending region: " + excerpt +
            "\nRespond with ONLY the JSON object, no explanation.")


def run_s2(model, story: str, master: dict | None = None, *,
           session_date: str | None = None,
           edus: list[dict] | None = None,
           stats: dict | None = None,
           core_only: bool | None = None) -> dict:
    """S2: story → embed list (draft prompt v1, owner-in-the-loop pending).

    Output is bounded at ``_S2_S4_MAX_TOKENS`` (M3 #1524, D2); a truncated
    or unparseable response raises ``_ParseError`` → census ``parse_error``
    (the tail-cut tolerance of ``_parse_json`` still recovers truncated
    JSON; the census records the truncation for the fix loop). S2 retries
    once on parse failure (``_complete_parsed`` — pilot #1549 fix).

    ``story`` threads into the render (A′ #1695 Task 2): the label-order
    shuffle seed derives from the story, so a paired canonical re-run under
    a different order reproduces the SAME session. NOTE (flag-off
    byte-identity scope): under ``TORTOISE_EXTRACTOR_PROMPT=compact`` the
    story ALSO story-filters the pack-kind injection — aligning S2 with
    S4's selection (origin ran S2 with story=None → full set). The DEFAULT
    verbose path is unaffected (story only feeds the seed, which is a
    no-op when the shuffle is unset), so the flag-off byte-identity claim
    holds for the default render mode."""
    return _complete_parsed(model,
                            render_s2_prompt(master, session_date=session_date,
                                             edus=edus, story=story,
                                             core_only=core_only),
                            "S1 STORY:\n" + story,
                            max_tokens=_stage_cap(_S2_S4_MAX_TOKENS),
                            stats=stats)


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


def _enrich_point_priors(sdk, points: list[dict]) -> None:
    """D7: ONE batched Cypher fetches aboutObject names + when/created_at
    for the candidate point ids and merges them INTO the row dicts (in
    place) — the classifier's entity gate (D2) + later-date guard (CG-2)
    inputs. Anti-N+1: a single query for the whole candidate set. Best-
    effort: any failure leaves the rows un-enriched (never degrades the
    search). Mock/embedded callers without a projection are skipped."""
    if not points:
        return
    get_proj = getattr(sdk, "_get_proj", None)
    if get_proj is None:
        return
    try:
        proj = get_proj()
    except Exception:  # noqa: BLE001, RUF100 — best-effort enrichment
        return
    ids = [str(p.get("id") or "") for p in points if p.get("id")]
    if not ids:
        return
    try:
        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.id IN $ids "
            "OPTIONAL MATCH (p)-[:aboutObject]->(o:Object) "
            "RETURN p.id, collect(o.name), p.when, p.createdAt",
            params={"ids": ids}).result_set
    except Exception:  # noqa: BLE001, RUF100 — enrichment is best-effort
        return
    by_id = {str(row[0]): row for row in rows}
    for p in points:
        row = by_id.get(str(p.get("id") or ""))
        if not row:
            continue
        names = [n for n in (row[1] or []) if n]
        if names:
            p["about_entities"] = names
        if row[2]:
            p["when"] = row[2]
        if row[3]:
            p["created_at"] = row[3]


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
        _enrich_point_priors(sdk, list(results["points"].values()))
        return {
            "mode": mode, "degraded": True,
            "reason": f"S3 degraded: graph search failed ({type(e).__name__}: {e})",
            "entities": list(results["entities"].values()),
            "points": list(results["points"].values()),
            "events": list(results["events"].values()),
            "queries_run": q_run,
        }
    _enrich_point_priors(sdk, list(results["points"].values()))
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
- RETRACTIONS (E7): when the conversation explicitly WITHDRAWS a previously-
  stated fact ("forget my gym schedule", "scratch that", "that is no longer
  true"), add {"content": "<the exact prior claim>"} or {"id": "<existing-id>"}
  to the top-level "retractions" array. Additive — emit nothing when nothing
  is withdrawn. Never retract from ambiguity — only explicit withdrawals.
- chain_notes: flag violations, TRY TO REPAIR toward the nearest valid chain
  position, never invent entities.
- link_before_create: record what you searched / what the graph already had.
- RETRACTIONS (E7): when the conversation explicitly WITHDRAWS a previously-
  stated fact ("forget my gym schedule", "scratch that", "that is no longer
  true"), add {"content": "<the exact prior claim>"} or {"id": "<existing-id>"}
  to the top-level "retractions" array. Additive — emit nothing when nothing
  is withdrawn. Never retract from ambiguity — only explicit withdrawals.
- Tier-A state-value points from the S2 list are NEVER dropped — re-emit
  them (with corrections if the search shows they exist). The S4 pass
  COMPLETES the list; it does not replace S2 findings. The value is the fact.
Empty arrays are valid. Print ONLY the JSON object."""


# #1695 Task 5: the flag-on S4 template — the base template with the S4
# re-emit clause (S2 items keep the classifier's kinds VERBATIM; the
# MUST-come-from-list rule applies to NEW items only) and the chains block
# replaced by the deterministic-enforcement note. Derived so the flag-off
# S4_TMPL stays byte-identical.
S4_TMPL_CORE_ONLY = S4_TMPL.replace(
    "MASTER LIST (same closed vocabulary as S2 — no minted kinds)\n{master_list}\n\n"
    "CHAINS\n{chains_text}\n\nS1 STORY",
    "MASTER LIST (CORE ONLY — the pack vocabulary is NOT here)\n{master_list}\n\n"
    "CHAIN ENFORCEMENT IS DETERMINISTIC (post-extraction graph pass).\n\n"
    "S1 STORY",
).replace(
    "- Re-emit the S2 items you keep, corrected where the search results show they\n"
    "  already exist (lifecycle changed/unchanged + supersedes = the existing id).",
    "- Re-emit the S2 items you keep, corrected where the search results show they\n"
    "  already exist (lifecycle changed/unchanged + supersedes = the existing id).\n"
    "- S2 ITEMS ARE TYPED (#1695): the S2 items in the input carry kinds assigned\n"
    "  by the classifier — including PACK kinds NOT in your MASTER LIST and the\n"
    "  \"unclassified\" sentinel. Re-emit them VERBATIM with their kinds UNCHANGED:\n"
    "  do NOT re-type an S2 item, do NOT replace a pack kind with a core kind, do\n"
    "  NOT resolve an \"unclassified\" you cannot see in the list. The\n"
    "  MUST-come-from-list rule applies to NEW items only.",
).replace(
    # FIX D (cycle 3): the base's advisory "TRY TO REPAIR" bullet contradicts
    # the deterministic-enforcement contract — swap it in the core-only
    # derivation (the base template stays byte-identical). The fragment is
    # asserted to exist exactly once in S4_TMPL (anchor pin).
    "- chain_notes: flag violations, TRY TO REPAIR toward the nearest valid chain\n"
    "  position, never invent entities.",
    "- chain_notes: flag violations for the deterministic post-extraction\n"
    "  enforcer — do NOT attempt repairs yourself (the enforcer rewires\n"
    "  deterministically). NEVER invent entities.",
)


def render_s4_prompt(story: str, search: dict, embed_list: dict,
                     master: dict | None = None, *,
                     session_date: str | None = None,
                     edus: list[dict] | None = None,
                     core_only: bool | None = None) -> str:
    """The S4 prompt. ``core_only`` (None = env fallback) selects the
    #1695 Task 5 core-only variant (pack vocabulary + chains out, the S4
    re-emit clause in); the flag-off default renders byte-identically."""
    master = master or build_master_list()
    if core_only is None:
        core_only = _classify_later_enabled()
    transcript = _render_source_transcript(edus)
    tmpl = S4_TMPL_CORE_ONLY if core_only else S4_TMPL
    contract = OUTPUT_CONTRACT_CORE_ONLY if core_only else OUTPUT_CONTRACT
    chains = "" if core_only else _render_chains(master)
    return (tmpl
            .replace("{master_list}", _render_master(
                master, story, core_only=core_only))
            .replace("{chains_text}", chains)
            .replace("{story}", story)
            .replace("{search_results}", _render_search_results(search))
            .replace("{embed_list_json}", json.dumps(embed_list, indent=1))
            .replace("{date_anchor}", _date_anchor(
                session_date, include_emission_rules=True))
            .replace("{output_contract}", contract)
            + (("\n\n" + transcript) if transcript else ""))


def run_s4(model, story: str, search: dict, embed_list: dict,
           master: dict | None = None, *,
           session_date: str | None = None,
           edus: list[dict] | None = None,
           stats: dict | None = None,
           core_only: bool | None = None) -> dict:
    """S4: complete the embed list (S2 + gaps). Draft prompt v1.

    Output bounded at ``_S2_S4_MAX_TOKENS`` (M3 #1524, D2); unparseable
    output → ``_ParseError`` → census ``parse_error`` (see ``run_s2``)."""
    return _complete_parsed(model,
                            render_s4_prompt(story, search, embed_list, master,
                                             session_date=session_date, edus=edus,
                                             core_only=core_only),
                            "Complete the embed list.",
                            max_tokens=_stage_cap(_S2_S4_MAX_TOKENS),
                            stats=stats)


# ── E4 (#1536): S4 merges-not-replaces — programmatic union (S2 ∪ S4) ──────

def _merge_key(section: str, item: dict) -> tuple:
    """E4 identity key for embed-list reconciliation (S2 ∪ S4)."""
    if section == "entities":
        return ("entity", _norm(item.get("name")), _norm_kind(item.get("kind")))
    if section in ("events", "points"):
        return (section, _norm(item.get("content")))
    if section == "operators":
        te = item.get("target_edge") or {}
        return ("op", _norm(item.get("src")), _norm(item.get("dst")),
                _norm(str(item.get("op_type"))),
                _norm(te.get("src")), _norm(te.get("dst")),
                _norm(str(te.get("op_type"))))
    if section == "chain_notes":
        return ("chain", _norm(item.get("chain")), _norm(item.get("finding")))
    if section == "link_before_create":
        return ("lbc", _norm(item.get("searched_for")), bool(item.get("found")))
    if section == "retractions":
        # E7 (D5): retraction refs dedupe by (content | id) — S4's
        # graph-informed version wins on collision (E4 union semantics).
        return ("retraction", _norm(str(item.get("content") or "")),
                _norm(str(item.get("id") or "")))
    return (section, json.dumps(item, sort_keys=True, default=str))


def merge_embed_lists(s2: dict, s4: dict) -> dict:
    """Programmatic union of the S2 and S4 embed lists (E4 — merges-not-replaces).

    S4 is the gap reviewer: on identity-key collision its (graph-informed)
    version wins; S2 items S4 omitted are PRESERVED (no silent drops); S4-only
    items append in S4 order. S2 order is preserved in place. Items pass
    through by reference — unknown fields ride through untouched (E1/E3).
    None sections and non-dict entries are tolerated (skipped).
    """
    sections = ("entities", "events", "points", "operators",
                "chain_notes", "link_before_create", "retractions")
    out: dict = {}
    for section in sections:
        s2_items = [i for i in (s2.get(section) or []) if isinstance(i, dict)]
        s4_items = [i for i in (s4.get(section) or []) if isinstance(i, dict)]
        s4_by_key = {_merge_key(section, i): i for i in s4_items}
        merged: list[dict] = []
        seen: set = set()
        for item in s2_items:
            k = _merge_key(section, item)
            if k in seen:
                continue                      # S2-side duplicate — first wins
            seen.add(k)
            merged.append(s4_by_key.get(k, item))   # S4 wins on collision; S2 never dropped
        for item in s4_items:
            k = _merge_key(section, item)
            if k not in seen:
                seen.add(k)
                merged.append(item)           # S4 gap addition
        # emit a section when EITHER input carries the key (empty lists
        # included — identity preserved; absent keys stay absent, so the
        # additive E7 ``retractions`` section never fabricates keys)
        if section in s2 or section in s4:
            out[section] = merged
    return out


def _s4_merge_stats(s2: dict, s4: dict, merged: dict) -> dict:
    """E4 observability: prove 'no silent drops' in a live run (M7-adjacent)."""
    sections = ("entities", "events", "points", "operators")
    s2_n = sum(len(s2.get(s) or []) for s in sections)
    s4_n = sum(len(s4.get(s) or []) for s in sections)
    merged_n = sum(len(merged.get(s) or []) for s in sections)
    s4_keys = {_merge_key(s, i)
               for s in sections for i in (s4.get(s) or [])
               if isinstance(i, dict)}
    corrected = sum(1 for s in sections for i in (s2.get(s) or [])
                    if isinstance(i, dict) and _merge_key(s, i) in s4_keys)
    return {
        "s2_items": s2_n,
        "s4_items": s4_n,
        "merged_items": merged_n,
        "corrected_by_s4": corrected,
        "kept_from_s2": max(0, s2_n - corrected),   # no-silent-drop counter
        "added_by_s4": max(0, merged_n - s2_n),
    }


# ── S5: EMBED — deterministic execution → Layer-1 payload ──────────────────

def _norm(s: str) -> str:
    """Normalize a classification key for identity comparisons. Defensive
    str() coercion (review FIX C): LLM-emitted items may carry numeric/
    non-str names/content — a raw .strip() would raise AttributeError on
    them and abort the whole union-classify block.
    """
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


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
            if _valid_iso_date(when) and _valid_iso_date(old_when):  # noqa: SIM102
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
    with a warning. The classify-later ``unclassified`` sentinel is carried
    WITHOUT the minted-kind repair warning (FIX G — it is a terminal, not a
    minted kind; unresolved refs are dropped downstream). Returns None when
    no role survived (the payload entry gets no slots).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        warnings.append(f"{ctx}: slots must be an object — dropped")
        return None
    master = master or {}
    # FIX M slot-lane consistency: the subject/object lane gates against the
    # SAME widened object vocabulary as execute_embed's entity gate
    # (_object_kind_forms — master + pack object/document kinds) — a slot
    # referencing an emitted pack-kind entity (e.g. dev:apiSpec) must keep
    # its kind and resolve, not be repaired to core:other and dropped.
    entity_forms = _object_kind_forms(master) if master else None
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
            # FIX G: the classify-later sentinel is a terminal, never a
            # minted kind — skip the minted-kind branch explicitly (as
            # _rekey_slots does) so it's carried without a spurious
            # "minted slot kind" warning (resolved at write, like the
            # entity/event sentinels).
            sentinel = kind.lower() == UNCLASSIFIED
            if role == "event":
                if sentinel:
                    # FIX O: the sentinel is only advertised for top-level
                    # fields — an EVENT-role slot passes through to the
                    # payload untouched (_resolve_slot_refs drops only
                    # subject/object strays, fail-closed), so carrying it
                    # would write kind="unclassified" into a slot. Repair
                    # to the event fallback SILENTLY (a terminal, not a
                    # minted kind — FIX G's no-noise intent).
                    kind = _EVENT_FALLBACK["kind"]
                elif kind.lower() not in event_forms and \
                        kind.lower().rsplit(":", 1)[-1] not in event_forms_bare:
                    warnings.append(f"minted slot kind {kind!r} ('{name[:60]}'"
                                    f") → repaired to {_EVENT_FALLBACK['kind']}")
                    kind = _EVENT_FALLBACK["kind"]
            elif entity_forms and not sentinel and \
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


_RESOLUTION_SYSTEM = (
    "You are the ENTITY RESOLUTION assistant for the Tortoise epistemic "
    "memory. You map NEW entity names from a conversation to EXISTING "
    "entities already in the knowledge graph. Resolve ONLY confident "
    "aliases: same referent, case/variant/abbreviation forms (\"Joe\" → "
    "\"Joseph\", \"NYC\" → \"New York City\"). NEVER guess: an ambiguous or "
    "unclear name must be omitted. Never invent ids. "
    "Output ONE JSON object: {\"resolutions\": [{\"name\": str, "
    "\"resolves_to\": str}]} where resolves_to is the existing entity's "
    "id or exact name."
)


def _resolution_prompt(existing: list[dict], new_names: list[str]) -> str:
    """D3 phase-2 prompt: the existing-entity candidate table + the new
    names to resolve (the JSON contract pinned in the prompt so the model
    output is parseable and validated against the table)."""
    lines = [f"- {e.get('id', '')} | {e.get('name', '')} | {e.get('kind', '')}"
             for e in existing if e.get("name")]
    table = "\n".join(lines) if lines else "(none)"
    names = "\n".join(f"- {n}" for n in new_names) if new_names else "(none)"
    return (f"EXISTING ENTITIES (id | name | kind):\n{table}\n\n"
            f"NEW ENTITY NAMES:\n{names}\n\n"
            "Return {\"resolutions\":[{\"name\": ..., \"resolves_to\": "
            "existing-id-or-name}]} — every resolution must match an id or "
            "exact name in the EXISTING ENTITIES table; ambiguous → omit.")


def resolve_entities(entity_refs: list[dict], search: dict,
                     model=None) -> dict:
    """D3: two-phase entity resolution — returns
    {"map": {name: {"id", "name"}}, "records": [{"name", "resolves_to",
    "mode"}], "warnings": [...]}.

    Phase 1 (deterministic): the existing ``_find_existing_entity`` (exact
    → bare → ambiguous) for every ref — no model call.
    Phase 2 (LLM fallback): fires ONLY when ``model`` is set AND the search
    has entity candidates AND >=1 ref is unmatched/ambiguous. ONE
    ``_complete`` call (existing 600s wall-clock bound; temperature 0.0 via
    the MODELS seam — adapters default temperature=0.0), strict JSON
    contract; every resolution validated against the candidate table (id
    or normalized-name match) — invalid/ambiguous dropped with a warning,
    never guessed.
    Failure/timeout → warning + phase-1-only map (degrade to ADD semantics
    at the caller — unresolved entities keep their names). NEVER raises.
    """
    existing = [e for e in (search or {}).get("entities", []) or []
                if isinstance(e, dict) and e.get("id")]
    warnings: list[str] = []
    resolved: dict[str, dict] = {}
    records: list[dict] = []
    unmatched: list[dict] = []
    for ref in entity_refs:
        name = str(ref.get("name") or "").strip()
        kind = str(ref.get("kind") or "").strip()
        if not name:
            continue
        ex, mode = _find_existing_entity(existing, name, kind)
        if ex is not None:
            resolved[name] = {"id": str(ex.get("id") or ""),
                              "name": str(ex.get("name") or name)}
            records.append({"name": name,
                            "resolves_to": resolved[name]["id"],
                            "mode": mode})
        else:
            unmatched.append({"name": name, "kind": kind, "mode": mode})
    # Phase 2 — bounded LLM fallback for the unmatched/ambiguous remainder
    if model is not None and existing and unmatched:
        try:
            prompt = _resolution_prompt(
                existing, [u["name"] for u in unmatched])
            resp = _complete(model, _RESOLUTION_SYSTEM, prompt,
                             max_tokens=500)
            parsed = _parse_json(resp)
            unmatched_names = {u["name"] for u in unmatched}
            for item in (parsed.get("resolutions") or []):
                if not isinstance(item, dict):
                    continue
                cand = str(item.get("name") or "").strip()
                target = str(item.get("resolves_to") or "").strip()
                if not cand or not target or cand not in unmatched_names:
                    if cand:
                        warnings.append(f"resolution {cand!r} not among the "
                                        "unmatched names — dropped")
                    continue
                hit = next((e for e in existing
                            if e.get("id") == target
                            or _norm(e.get("name", "")) == _norm(target)),
                           None)
                if hit is None:
                    warnings.append(f"resolution {cand!r} → {target!r} does "
                                    "not match an existing entity — dropped")
                    continue
                resolved[cand] = {"id": str(hit.get("id") or ""),
                                  "name": str(hit.get("name") or cand)}
                records.append({"name": cand,
                                "resolves_to": resolved[cand]["id"],
                                "mode": "llm"})
        except Exception as e:  # noqa: BLE001, RUF100 — degrade, never block
            warnings.append(f"entity-resolution LLM fallback failed "
                            f"({type(e).__name__}: {e}) — phase-1 results "
                            "only (degrade to ADD)")
    return {"map": resolved, "records": records, "warnings": warnings}


def _apply_entity_resolution(embed_list: dict, resolution_map: dict) -> dict:
    """D3: rewrite the embed list's entity names + about_entities/slot refs
    to the canonical existing name (deep copy — the caller keeps the raw
    S2/S4 list for provenance). Operator endpoints reference CONTENT, not
    names — untouched."""
    import copy
    out = copy.deepcopy(embed_list or {})
    name_map = {k: v.get("name", k) for k, v in (resolution_map or {}).items()}
    if not name_map:
        return out
    for e in out.get("entities") or []:
        if isinstance(e, dict) and e.get("name") in name_map:
            e["name"] = name_map[e["name"]]
    for section in ("points", "events"):
        for item in out.get(section) or []:
            if not isinstance(item, dict):
                continue
            about = item.get("about_entities") or []
            item["about_entities"] = [name_map.get(a, a) for a in about
                                       if isinstance(a, str)]
            slots = item.get("slots")
            if isinstance(slots, dict):
                for role in ("subject", "object"):
                    for s in slots.get(role) or []:
                        if isinstance(s, dict) and s.get("name") in name_map:
                            s["name"] = name_map[s["name"]]
    return out


# ── E7 (#1539): the 4-way consolidation classifier (D1) ───────────────────

# Tunable via the run protocol (Q2 first-cut): REVISES_MIN_OVERLAP reuses
# E5's existing band; NOOP_MIN_OVERLAP is the paraphrase-NOOP floor.
REVISES_MIN_OVERLAP = 0.6      # E5 #1537: supersede-by-correction band
NOOP_MIN_OVERLAP = 0.45        # Q2: paraphrase-NOOP band floor


class DecisionRecord:
    """One 4-way consolidation decision (D1).

    decision ∈ {"ADD", "UPDATE", "NOOP", "DELETE"}; prior_id = the matched
    existing point ("" for ADD); overlap = the length-guarded token overlap
    vs the chosen prior; reason ∈ {"", "identical", "paraphrase",
    "value_change"}; evidence = human-readable why (rides the result-level
    records + link_before_create notes).
    """

    __slots__ = ("decision", "evidence", "overlap", "prior_id", "reason")

    def __init__(self, decision: str, prior_id: str = "",
                 overlap: float = 0.0, reason: str = "",
                 evidence: str = "") -> None:
        self.decision = decision
        self.prior_id = prior_id
        self.overlap = overlap
        self.reason = reason
        self.evidence = evidence


_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}

_MINUTE_WORDS = {"half": 30, "quarter": 15, "thirty": 30, "fifteen": 15,
                  "forty": 40, "forty-five": 45, "forty five": 45}


def _num_word_value(s: str) -> int | None:
    """Digit value of an English number word ("six" → 6, "twenty three" →
    23); None when not a number word."""
    s = (s or "").strip().lower()
    if s in _NUMBER_WORDS:
        return _NUMBER_WORDS[s]
    parts = re.split(r"[\s-]+", s)
    if len(parts) == 2 and parts[0] in _NUMBER_WORDS \
            and parts[1] in _NUMBER_WORDS:
        tens, ones = _NUMBER_WORDS[parts[0]], _NUMBER_WORDS[parts[1]]
        if tens >= 20 and 0 <= ones <= 9:
            return tens + ones
    return None


_NUM_WORD_ALT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))


def _value_signature(content: str) -> str | None:
    """Deterministic value-token normalization (D2, Q3) — the value-identity
    oracle behind the Tier-A NOOP-vs-UPDATE decision: "6pm"/"six pm"/
    "6:00 pm" → "p06:00"; "27:12" → "27:12"; "27m12s" → "27m12s"; "5k" →
    "5k". Returns None when the content carries no value-shaped token
    (Tier-B → the classifier falls back to the bands). Deterministic-but-
    curated (Q3): the vocabulary is deliberately small — clock times,
    compound numbers, and quantity+unit tokens. The clock branch is the
    single authority for HH(:MM) am/pm forms (the compound pass excludes
    them via lookahead so "6:00 pm" never double-signatures)."""
    c = _norm(content)
    sigs: list[str] = []
    # clock forms: "6pm" | "6:00 pm" | "six pm" | "six thirty pm" — the
    # hour word is constrained to the number-word vocabulary so "at six pm"
    # cannot greedily capture "at six" as the hour (deterministic).
    clock_re = re.compile(
        rf"\b(\d{{1,2}}(?::\d{{2}})?|(?:{_NUM_WORD_ALT})(?:[\s-]+"
        rf"(?:{_NUM_WORD_ALT}))?)\s*(a\.?m\.?|p\.?m\.?)\b")
    for m in clock_re.finditer(c):
        raw, amp = m.group(1), m.group(2)[0].lower()
        if ":" in raw:
            hh, mm = raw.split(":")
            sigs.append(f"{amp}{int(hh) % 12 or 12:02d}:{mm}")
        elif raw.isdigit():
            sigs.append(f"{amp}{int(raw) % 12 or 12:02d}:00")
        else:
            words = raw.split()
            hour_v = _num_word_value(words[0])
            if hour_v is None:
                continue
            minute_v = (_MINUTE_WORDS.get(" ".join(words[1:]), 0)
                        if len(words) > 1 else 0)
            sigs.append(f"{amp}{hour_v % 12 or 12:02d}:{minute_v:02d}")
    # compound numbers: "27:12", "1:02:30", "27m12s" (clock forms already
    # captured above — a following am/pm excludes the compound pass)
    for m in re.finditer(
            r"\b\d{1,4}(?::\d{2})+(?:\.\d+)?\b(?!\s*(?:a\.?m\.?|"
            r"p\.?m\.?))|\b\d+m\d+s\b", c):
        sigs.append(re.sub(r"\s+", "", m.group(0)))
    # quantity+unit: "5k", "10km", "2 hours"
    for m in re.finditer(
            r"\b\d+(?:\.\d+)?\s*(?:k|km|mi|m|kg|lb|min|mins|h|hr|hrs|"
            r"s|sec|secs|minutes|hours)\b", c):
        sigs.append(re.sub(r"\s+", "", m.group(0)))
    if not sigs:
        return None
    return "|".join(sorted(set(sigs)))


def _entity_gate(prior: dict, entity_mentions: list[str]) -> bool:
    """D2 entity gate: the candidate's resolved entity mention co-mentions
    in the prior's content (eval proxy — S3 point rows carry content only)
    OR matches the prior's aboutObject names (production graph / Task 4
    enrichment). No mention at all → undetermined → True (never block on
    absent data — the bands decide)."""
    mentions = [str(m).strip().lower() for m in (entity_mentions or [])
                if isinstance(m, str) and m.strip()]
    if not mentions:
        return True
    prior_norm = _norm(str(prior.get("content") or ""))
    if any(m in prior_norm for m in mentions):
        return True
    prior_ents = [str(a).strip().lower()
                  for a in (prior.get("about_entities") or [])
                  if isinstance(a, str) and a.strip()]
    return any(m == a for m in mentions for a in prior_ents)


def _attribute_gate(point: dict, prior: dict) -> bool:
    """D2 attribute gate: search_keys overlap (E3) when BOTH sides carry
    keys; either side lacking keys → undetermined → True (the value-
    signature / band decides)."""
    keys_new = {_norm(k) for k in (point.get("search_keys") or [])
                if isinstance(k, str) and k.strip()}
    keys_old = {_norm(k) for k in (prior.get("search_keys") or [])
                if isinstance(k, str) and k.strip()}
    if not keys_new or not keys_old:
        return True
    return bool(keys_new & keys_old)


def _date_is_later_or_undated(current_date: str | None, prior: dict) -> bool:
    """CG-2 later-date guard: when BOTH the candidate's session date and the
    prior's when/created_at/createdAt are valid ISO dates, the candidate's
    must be >= the prior's; either absent → True (session-ingest order
    supplies the invariant)."""
    if not current_date:
        return True
    old_when = str(prior.get("when") or prior.get("created_at")
                   or prior.get("createdAt") or "").strip()
    if not old_when:
        return True
    try:
        if _valid_iso_date(str(current_date)) and _valid_iso_date(old_when):
            return str(current_date)[:10] >= old_when[:10]
    except (TypeError, ValueError):
        pass
    return True


def classify_consolidation(point: dict, priors: list[dict], *,
                           entity_mentions: list[str] | None = None,
                           current_date: str | None = None) -> DecisionRecord:
    """D1: the pure 4-way consolidation classifier (no LLM, no graph I/O).

    Maps (candidate point + S3-retrieved priors + entity/attribute context)
    to one of ADD / UPDATE / NOOP / DELETE in priority order:

      1. NOOP (identical)   — normalized content equals a prior (the
                              existing exact/content-hash dedup);
      2. UPDATE             — same entity + same attribute, VALUE differs
                              (value-signature inequality when both sides
                              carry a signature, else the E5 frame-token
                              diff), length-guarded overlap ≥
                              REVISES_MIN_OVERLAP OR the E5 entity-grounded
                              contradiction pass, and the session date is
                              not before the prior's (CG-2);
      3. NOOP (paraphrase)  — equal value-signature with an explicit entity
                              mention (or the Tier-A marker) + the gates,
                              OR overlap ≥ NOOP_MIN_OVERLAP with the gates,
                              OR high overlap with an AMBIGUOUS gate (never
                              UPDATE);
      4. ADD                — no prior match.

    DELETE is NEVER produced from content alone (D5 — only explicit
    retractions). Ambiguous entity/attribute → NOOP only on high text
    overlap, else ADD — never UPDATE (E2E-11 owned negative). Self-match is
    impossible by construction: identical content → NOOP (step 1), changed
    content → new content-addressed id ≠ prior id (E5's supersede guard
    remains the backstop).

    ``priors`` are the S3 point rows ({id, content, kind} + the Task 4
    enrichment fields about_entities/when/created_at when available).
    """
    content = str(point.get("content") or "").strip()
    if not content:
        return DecisionRecord("ADD", evidence="empty candidate content")
    norm = _norm(content)
    mentions = [str(m) for m in (entity_mentions or [])
                if isinstance(m, str) and m.strip()]
    tier_a = str(point.get("tier") or "").strip().upper() == "A"

    # 1) NOOP — identical (existing exact/content-hash dedup)
    for p in priors:
        if _norm(str(p.get("content") or "")) == norm:
            return DecisionRecord("NOOP", prior_id=str(p.get("id") or ""),
                                  overlap=1.0, reason="identical",
                                  evidence="exact normalized content match")

    best_update: tuple[float, dict] | None = None
    best_noop: tuple[float, dict, str] | None = None   # (overlap, prior, mode)
    for p in priors:
        if not str(p.get("id") or ""):
            continue
        old_content = str(p.get("content") or "")
        ov = _token_overlap(old_content, content)
        gate = _entity_gate(p, mentions) and _attribute_gate(point, p)
        sig_new = _value_signature(content)
        sig_old = _value_signature(old_content)
        both_sigs = bool(sig_new and sig_old)
        sig_equal = both_sigs and sig_new == sig_old
        if both_sigs:
            value_differs = not sig_equal
        else:
            value_differs = bool(_frame_tokens(content)
                                 - _frame_tokens(old_content))
        later = _date_is_later_or_undated(current_date, p)
        # 2) UPDATE — priority over NOOP
        if gate and later and value_differs:
            band_ok = ov >= REVISES_MIN_OVERLAP
            contradiction = _fact_value_contradiction(
                content, mentions, p, when=current_date)
            if (band_ok or contradiction) \
                    and (best_update is None or ov > best_update[0]):
                best_update = (ov, p)
        # 3) NOOP — paraphrase
        if sig_equal and (tier_a or bool(mentions)) and gate:
            # value identity — short-circuits both bands
            if best_noop is None or ov > best_noop[0]:
                best_noop = (ov, p, "value_signature_equal")
        elif ov >= NOOP_MIN_OVERLAP:
            # band with the gates, or ambiguous-but-high overlap (never
            # UPDATE — E2E-11 owned negative)
            mode = "overlap_band" if gate else "ambiguous_high_overlap"
            if best_noop is None or ov > best_noop[0]:
                best_noop = (ov, p, mode)

    if best_update is not None:
        ov, p = best_update
        return DecisionRecord(
            "UPDATE", prior_id=str(p.get("id") or ""), overlap=round(ov, 3),
            reason="value_change",
            evidence="same entity+attribute, value differs, length-guarded "
                     "overlap or fact-value contradiction, later date")
    if best_noop is not None:
        ov, p, mode = best_noop
        return DecisionRecord(
            "NOOP", prior_id=str(p.get("id") or ""), overlap=round(ov, 3),
            reason="paraphrase",
            evidence=f"{mode} (overlap {ov:.2f})")
    return DecisionRecord("ADD", evidence="no prior match")


def _find_point_match(points: list[dict], content: str, *,
                       about_entities: list[str] | None = None,
                       when: str | None = None) -> tuple[str, str]:
    """(match_kind, existing_id) compatibility shim over
    ``classify_consolidation`` (E7): 'exact' (NOOP — identical or
    paraphrase), 'revises' (UPDATE), or ('none', ''). Kept so the E5-level
    unit surface (``_token_overlap``/``_fact_value_contradiction`` tests)
    stays green; ``execute_embed`` calls the classifier directly.
    """
    dec = classify_consolidation(
        {"content": content, "about_entities": about_entities or []},
        points, entity_mentions=about_entities, current_date=when)
    if dec.decision == "NOOP":
        return "exact", dec.prior_id
    if dec.decision == "UPDATE":
        return "revises", dec.prior_id
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


def validate_chain_completeness(embed_list: dict,
                                 master: dict | None = None) -> list[dict]:
    """Rules-with-why chain completeness (issue #1933, E2E-4 negative a).

    A pack chain whose FIRST emitted step is missing its NEXT step is an
    INCOMPLETE chain: e.g. the agent-ops ``ruleLifecycle`` chain
    [rule, rationale, ruleRevised] — a rule emitted WITHOUT its rationale
    (the why) breaks the rules-with-why contract. Deterministic, no LLM:

    1. Collect every emitted kind (entities/points/events, bare form) from
       the embed list (PRE-repair — pack point kinds like
       ``agent-ops:rationale`` are still visible here, unlike the commit
       payload where FIX P repairs them to ``statement``).
    2. For each PACK-DECLARED chain whose id is NOT in the canonical
       hardcoded ``CHAINS`` dict (productDelivery/epicToCode/
       campaignToChannel — their enforcement semantics are established via
       the graph/payload validators and must not change), find the LOWEST
       step index with an emitted item.
    3. If the NEXT step (index+1) has NO emitted item → warn, naming the
       missing step. A ruleRevised-only embed (highest step emitted, no
       next step) never warns — a revision without its rule is outside this
       chain's completeness contract.
    4. Zero emitted items for ANY step → no note, no warning (unrelated
       sessions never flood warnings).

    Severity is the chain's manifest enforcement (warn — never blocks).
    Returns notes mirroring ``validate_chains``' format.
    """
    master = master or build_master_list()
    emitted: set[str] = set()
    for e in (embed_list.get("entities") or []):
        if isinstance(e, dict) and e.get("kind"):
            emitted.add(str(e["kind"]).rsplit(":", 1)[-1].lower())
    for p in (embed_list.get("points") or []):
        if isinstance(p, dict) and p.get("pointKind"):
            emitted.add(str(p["pointKind"]).rsplit(":", 1)[-1].lower())
    for ev in (embed_list.get("events") or []):
        if isinstance(ev, dict) and ev.get("eventKind"):
            emitted.add(str(ev["eventKind"]).rsplit(":", 1)[-1].lower())
    if not emitted:
        return []
    try:
        from tortoise.pack_registry import PackRegistry, default_packs_dir
        reg = PackRegistry(default_packs_dir())
        reg.load_all()
    except Exception:  # noqa: BLE001, RUF100 — never block capture
        return []
    notes: list[dict] = []
    for pack in reg.packs.values():
        for chain in getattr(pack, "chains", []) or []:
            cid = chain.get("id")
            steps = [str(s) for s in (chain.get("steps") or [])]
            if not cid or len(steps) < 2 or cid in CHAINS:
                continue  # canonical chains keep their established semantics
            idx = {s.lower(): i for i, s in enumerate(steps)}
            present = [i for s, i in idx.items() if s in emitted]
            if not present:
                continue  # chain not in play for this session
            first = min(present)
            nxt = first + 1
            if nxt >= len(steps):
                continue  # chain ends at its first emitted step — complete
            missing = steps[nxt]
            if idx.get(missing.lower()) is not None and \
                    missing.lower() not in emitted:
                notes.append({
                    "chain": cid,
                    "finding": (f"chain '{cid}' emitted '{steps[first]}' but "
                                f"its next step '{missing}' is missing — the "
                                f"chain is incomplete"),
                    "action": "warned",
                    "note": (f"'{missing}' is the next declared chain step "
                             f"({ ' → '.join(steps) }); per chain enforcement "
                             f"'{chain.get('enforcement', 'warn')}' this is a "
                             "warning, never a block"),
                })
    return notes


def _minted_kind_report(embed_list: dict, master: dict | None = None) -> list[str]:
    """Every kind used in entities/events/points that is NOT writable by
    the matching write gate (indicator: 0 minted kinds). Each lane uses the
    SAME vocabulary as its execute_embed gate: entities → the EXTENDED
    object vocabulary (master + pack object/document kinds — FIX M),
    events → the EXTENDED event vocabulary (FIX A), points → the master's
    "points" section ONLY (FIX P — pack point kinds are never writable)."""
    master = master or build_master_list()
    obj_forms = _object_kind_forms(master)
    full = {k.lower() for k in obj_forms if ":" in k}
    bare = {k.lower().rsplit(":", 1)[-1] for k in obj_forms}
    point_full = {k.lower() for k in master["points"]}
    point_bare = {k.lower().rsplit(":", 1)[-1] for k in master["points"]}
    minted: list[str] = []
    for e in embed_list.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        k = str(e.get("kind", ""))
        # The unclassified sentinel is a reserved terminal, not a minted
        # kind — below-floor items on the flag-on path carry it through
        # to the report and must NOT be flagged (the write path resolves
        # it with its own census).
        # entities: the EXTENDED object vocabulary (master + pack declared
        # object/document kinds — FIX M: a classifier-assigned synthesized
        # object kind is writable at execute_embed, so it is NOT minted;
        # the report must agree with the write gate).
        if k and k.lower() != UNCLASSIFIED and k.lower() not in full and k.lower() not in bare:
            minted.append(f"{k} (entity '{e.get('name', '')[:60]}')")
    for ev in embed_list.get("events", []) or []:
        if not isinstance(ev, dict):
            continue
        k = str(ev.get("eventKind", ""))
        # events: the EXTENDED event vocabulary (core + pack declared event
        # kinds — FIX A: a classifier-assigned pack event kind is writable at
        # execute_embed, so it is NOT minted; the report must agree with the
        # write gate).
        if k and k.lower() != UNCLASSIFIED and \
                k.lower() not in _event_kind_forms(master):
            minted.append(f"{k} (event '{ev.get('content', '')[:60]}')")
    for p in embed_list.get("points", []) or []:
        if not isinstance(p, dict):
            continue
        k = str(p.get("pointKind", ""))
        # points: the master's "points" section ONLY (full + bare forms) —
        # the point write gate repairs EVERYTHING else to statement, so the
        # report must flag pack point kinds WITH kindDefs (dev:requirement,
        # product-strategy:useCase/...) that master_kind_forms would
        # otherwise accept (FIX P — the report agrees with the gate).
        if k and k.lower() != UNCLASSIFIED and \
                k.lower() not in point_full and k.lower() not in point_bare:
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


def _resolve_retraction(ref: dict, search: dict,
                        *, warnings: list[str]) -> dict | None:
    """D5: resolve an embed-list retraction ref to an S3 prior POINT using
    the never-guess discipline: by id (unique), else normalized-content
    match; 0 or >1 matches → None (the caller warns + fails open). S3 only
    returns live priors (terminal excluded at the search layer, #1391), so
    a resolved deletion target is live by construction."""
    rid = str(ref.get("id") or "").strip()
    content = str(ref.get("content") or "").strip()
    points = [p for p in (search or {}).get("points", []) or []
              if isinstance(p, dict) and p.get("id")]
    if rid:
        for p in points:
            if str(p.get("id")) == rid:
                return p
        warnings.append(f"retraction id={rid!r} matches no S3 prior — "
                        "skipped (fail-open)")
        return None
    if content:
        norm = _norm(content)
        matches = [p for p in points
                   if _norm(str(p.get("content") or "")) == norm]
        if not matches:
            warnings.append(f"retraction content {content[:60]!r} matches no "
                            "S3 prior — skipped (fail-open)")
            return None
        if len(matches) > 1:
            warnings.append(f"retraction content {content[:60]!r} is "
                            f"ambiguous ({len(matches)} priors) — skipped "
                            "(never guess)")
            return None
        return matches[0]
    warnings.append("empty retraction ref (no id, no content) — skipped")
    return None


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

#: The pack-DECLARED event kinds (eventKinds) — including the kindDefs-less
#: ones: the classifier can assign them via the kind index's "events"
#: section (FIX L synthesis), so the write gate must accept them (FIX A
#: candidate/write-gate alignment). Full + bare forms, case-folded.
#: Derived once per process from the default packs (packs are static per
#: process — mirrors the other vocab caches).
_PACK_EVENT_FORMS: set[str] | None = None


def _event_kind_forms(master: dict) -> set[str]:
    """The writable event-kind vocabulary (FIX A candidate/write-gate
    alignment): the master's event forms (core EVENTS + pack kindDefs —
    the entity-gate mirror, ``master_kind_forms``) PLUS the namespaced
    pack DECLARED event kinds (eventKinds — including kindDefs-less ones
    the classifier can assign). Full + bare forms, case-folded. The gate
    must never raise: a pack-registry failure degrades to the
    master-forms-only set."""
    global _PACK_EVENT_FORMS
    forms = master_kind_forms(master)
    if _PACK_EVENT_FORMS is None:
        _PACK_EVENT_FORMS = set()
        try:
            from tortoise.pack_registry import (
                PackRegistry,
                default_packs_dir,
            )
            packs_dir = default_packs_dir()
            reg = PackRegistry(packs_dir)
            reg.load_all()
            for ns, pack in reg.packs.items():
                for k in (pack.event_kinds or []):
                    _PACK_EVENT_FORMS.add(f"{ns}:{k}".lower())
                    _PACK_EVENT_FORMS.add(k.lower())
        except Exception:  # noqa: BLE001, RUF100 — never let the write
            # gate raise (fail-open to the master-forms-only gate)
            _PACK_EVENT_FORMS = set()
    return forms | _PACK_EVENT_FORMS


#: The pack-DECLARED object/document kinds (objectKinds + documentKinds) —
#: including the kindDefs-less ones: the classifier can assign them via the
#: kind index's "objects" section (FIX L synthesis), so the entity write
#: gate must accept them (FIX M candidate/write-gate alignment — the events
#: lane's FIX A mirror). Full + bare forms, case-folded. Derived once per
#: process from the default packs (packs are static per process — mirrors
#: _PACK_EVENT_FORMS).
_PACK_OBJECT_FORMS: set[str] | None = None


def _object_kind_forms(master: dict) -> set[str]:
    """The writable entity-kind vocabulary (FIX M candidate/write-gate
    alignment): the master's object/subject/point/event forms
    (``master_kind_forms``) PLUS the namespaced pack DECLARED object and
    document kinds (objectKinds + documentKinds — including kindDefs-less
    ones the classifier can assign, e.g. dev:apiSpec, pm:milestone,
    marketing:keyword). Full + bare forms, case-folded. The gate must never
    raise: a pack-registry failure degrades to the master-forms-only set
    (mirrors _event_kind_forms)."""
    global _PACK_OBJECT_FORMS
    forms = master_kind_forms(master)
    if _PACK_OBJECT_FORMS is None:
        _PACK_OBJECT_FORMS = set()
        try:
            from tortoise.pack_registry import (
                PackRegistry,
                default_packs_dir,
            )
            packs_dir = default_packs_dir()
            reg = PackRegistry(packs_dir)
            reg.load_all()
            for ns, pack in reg.packs.items():
                for k in (pack.object_kinds or []) + \
                        (pack.document_kinds or []):
                    _PACK_OBJECT_FORMS.add(f"{ns}:{k}".lower())
                    _PACK_OBJECT_FORMS.add(k.lower())
        except Exception:  # noqa: BLE001, RUF100 — never let the write
            # gate raise (fail-open to the master-forms-only gate)
            _PACK_OBJECT_FORMS = set()
    return forms | _PACK_OBJECT_FORMS


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
        # FIX M candidate/write-gate alignment: the entity gate uses the
        # EXTENDED object vocabulary (master + pack object/document kinds —
        # the classifier's synthesized object kinds, e.g. dev:apiSpec,
        # pm:milestone, marketing:keyword, must survive un-repaired).
        forms = _object_kind_forms(master)
        if kind.lower() == UNCLASSIFIED:
            # #1695 Task 5: the classify-later sentinel is NEVER written to
            # the graph — best core kind + warning (the orchestrator's
            # census already counted the terminal).
            warnings.append(f"entity '{name[:60]}' kind 'unclassified' → "
                            f"repaired to {_ENTITY_FALLBACK['kind']} "
                            "(reserved sentinel)")
            kind = _ENTITY_FALLBACK["kind"]
        elif kind.lower() not in forms and \
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
        if ekind.lower() == UNCLASSIFIED:
            warnings.append(f"event '{content[:60]}' kind 'unclassified' → "
                            f"repaired to {_EVENT_FALLBACK['kind']} "
                            "(reserved sentinel)")
            ekind = _EVENT_FALLBACK["kind"]
        elif ekind.lower() not in _event_kind_forms(master):
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
    noops: list[dict] = []           # E7 (D4): folded duplicates — result-level
    for p in embed_list.get("points", []) or []:
        if not isinstance(p, dict):
            warnings.append(f"non-dict point entry {p!r} skipped")
            continue
        content = str(p.get("content", "")).strip()[:1000]
        if not content:
            continue
        pkind = str(p.get("pointKind", "")).strip() or "statement"
        if pkind.lower() == UNCLASSIFIED:
            warnings.append(f"point '{content[:60]}' kind 'unclassified' → "
                            f"repaired to {_POINT_FALLBACK['kind']} "
                            "(reserved sentinel)")
            pkind = _POINT_FALLBACK["kind"]
        elif pkind.lower() not in {k.lower() for k in master["points"]}:
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
        # E7 (D1): the 4-way decision vs the S3-retrieved priors — pure and
        # deterministic. NOOP folds into the existing point (a `duplicates`
        # stamp at the write path, D4 — physically ONE point, no new edge),
        # never a new payload point.
        decision = classify_consolidation(
            {"content": content, "about_entities": about_list,
             "search_keys": search_keys, "when": when_valid,
             "tier": str(p.get("tier") or "")},
            idx["points"], entity_mentions=about_list,
            current_date=when_valid or session_date)
        pid = _content_id("pt", content)
        if decision.decision == "NOOP":
            # D4: no payload point — the record rides result["noops"] and
            # the eval write path stamps duplicates + the CONTAINS link.
            # point_ids maps the content to the EXISTING id so operators
            # referencing the folded point resolve to the canonical one.
            pid = decision.prior_id or pid
            noops.append({
                "point_id": pid, "session_ref": session_id,
                "overlap": decision.overlap,
                "evidence": decision.evidence,
                "reason": decision.reason})   # "identical" | "paraphrase"
            point_ids[n] = pid
            link_before_create.append({
                "searched_for": f"point '{content[:60]}'", "found": True,
                "note": f"duplicate of existing {pid} ({decision.reason}) — "
                        "folded (duplicates stamp, no new point)"})
            continue
        if decision.decision == "UPDATE":
            reason = "REVISES"  # supersession: new content corrects the old
            existing_id = decision.prior_id
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

    # ── retractions (D5): explicit withdrawals → DELETE-soft records ──────
    # Never from content alone: only the embed list's additive `retractions`
    # refs (resolved with the never-guess discipline) produce deletions.
    # Records stay RESULT-level (D8) — the Layer-1 payload does not grow.
    deletions: list[dict] = []
    for ref in embed_list.get("retractions", []) or []:
        if not isinstance(ref, dict):
            warnings.append(f"non-dict retraction ref {ref!r} skipped")
            continue
        prior = _resolve_retraction(ref, search, warnings=warnings)
        if prior is None:
            continue
        rid = str(prior.get("id") or "").strip()
        if not rid:
            continue
        deletions.append({
            "point_id": rid,
            "evidence": f"explicit retraction in session {session_id} — "
                         "conversation withdrew the fact",
        })

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
        "noops": noops,                # E7 (D4): result-level, NOT in the payload
        "deletions": deletions,        # E7 (D5): result-level, NOT in the payload
        "supersessions": supersessions,
        "chain_notes": llm_chain_notes + chain_notes,
        "link_before_create": link_before_create,
        "warnings": warnings,
        "minted_kinds": minted,
        "stats": {
            "entities": len(payload_entities), "events": len(payload_events),
            "points": len(payload_points), "operators": len(payload_operators),
            "tier_a_points": tier_a_points,
            "noops": len(noops),
            "deletions": len(deletions),
            "search_queries": (search or {}).get("queries_run", 0),
            "search_degraded": bool((search or {}).get("degraded")),
            "chain_notes": len(chain_notes),
            "supersessions": len(supersessions),
        },
    }


# ── #1695 Task 5: classify-later stage helpers ────────────────────────────
# (pure embed-list transforms — no LLM here; the classifier owns the LLM)

#: The unclassified sentinel — shared with tortoise.kind_classifier (kept
#: local so extractor_v2 never imports the classifier at module level).
UNCLASSIFIED = "unclassified"

_CLASSIFY_SECTIONS = (
    ("entities", "kind", "entity"),
    ("events", "eventKind", "event"),
    ("points", "pointKind", "point"),
)


def _classify_item_id(section: str, item: dict) -> str:
    """Stable per-item key across classify passes: section + normalized
    name/content (index-independent — the merged list reorders)."""
    key = str(item.get("name") or item.get("content") or "")
    return f"{section}:{_norm(key)}"

def _collect_classify_items(embed_list: dict) -> list[dict]:
    """Items needing classification: entities/events/points whose kind is
    missing or the ``unclassified`` sentinel (core kinds assigned
    in-context stay). The classification surface is the name (entities) or
    content (events/points)."""
    items: list[dict] = []
    for section, kind_field, item_type in _CLASSIFY_SECTIONS:
        for i, item in enumerate(embed_list.get(section) or []):
            if not isinstance(item, dict):
                continue
            kind = str(item.get(kind_field) or "").strip()
            if kind and kind.lower() != UNCLASSIFIED:
                continue
            text = str(item.get("name") or item.get("content") or "").strip()
            if not text:
                continue
            items.append({"id": f"{_classify_item_id(section, item)}#{i}",
                          "type": item_type,
                          "text": text,
                          "section": section,
                          "kind": kind,
                          "_idx": i})
    return items


def _apply_classify_kinds(embed_list: dict, assignments: dict) -> dict:
    """Write the classifier assignments back into the embed list. The
    ``unclassified`` sentinel stays (the write path resolves it); every
    other assigned kind lands on the item's kind field."""
    for section, kind_field, _item_type in _CLASSIFY_SECTIONS:
        for i, item in enumerate(embed_list.get(section) or []):
            if not isinstance(item, dict):
                continue
            a = assignments.get(f"{_classify_item_id(section, item)}#{i}")
            if not a:
                continue
            kind = str(a.get("kind") or "")
            if not kind or kind == UNCLASSIFIED:
                continue
            item[kind_field] = kind
    return embed_list


def _s2_kind_register(s2_list: dict, s2_assignments: dict) -> dict:
    """{section-identity: classifier kind} for S2 items — the kind-
    preservation register the post-merge re-stamp consults (entities keyed
    by name, events/points by content — the section-aware freeze: freezing
    ``objects:plan`` never freezes ``subjects:plan``)."""
    register: dict[str, str] = {}
    for section, _kind_field, _item_type in _CLASSIFY_SECTIONS:
        for i, item in enumerate(s2_list.get(section) or []):
            if not isinstance(item, dict):
                continue
            a = s2_assignments.get(f"{_classify_item_id(section, item)}#{i}")
            if not a:
                continue
            kind = str(a.get("kind") or "")
            if not kind or kind == UNCLASSIFIED:
                continue
            register[f"{section}:{_identity_key(section, item)}"] = kind
    return register


def _identity_key(section: str, item: dict) -> str:
    """The section-aware kind-freeze identity (E4 merge-key semantics minus
    the kind, so a re-typed S4 duplicate is caught). Coerces via str() —
    a numeric/non-str name or content must not raise (review FIX C)."""
    if section == "entities":
        return _norm(str(item.get("name") or ""))
    return _norm(str(item.get("content") or ""))


def _restamp_s2_kinds(merged: dict, register: dict,
                      warnings: list[str]) -> int:
    """Kind preservation (post-E4): S2 classifier kinds survive S4
    re-emission. Every merged item whose section-aware identity matches a
    registered S2 item but whose kind was lost (missing/unclassified) is
    re-stamped with the S2 kind. The fold removes ONLY sentinel/missing/
    identical duplicates — a same-name member carrying a DIFFERENT
    non-sentinel kind is preserved as a distinct (name, kind) :Object and
    is NEVER folded or re-stamped (the S2 re-stamp fills LOST kinds only;
    cycle-3 P2). Returns the override count (observable via census)."""
    overrides = 0
    for section, kind_field, _item_type in _CLASSIFY_SECTIONS:
        items = merged.get(section) or []
        # entity name-collision fold: same identity, conflicting kinds →
        # keep the one carrying a registered S2 kind, drop the duplicates
        if section == "entities":
            by_name: dict[str, list[dict]] = {}
            for it in items:
                if isinstance(it, dict):
                    by_name.setdefault(_norm(str(it.get("name") or "")), []).append(it)
            for name, group in by_name.items():
                if len(group) <= 1:
                    continue
                s2_reg = register.get(f"entities:{name}")
                if s2_reg is None:
                    continue
                keeper = next(
                    (it for it in group
                     if str(it.get(kind_field) or "").strip() == s2_reg), group[0])
                for it in list(group):
                    if it is keeper:
                        continue
                    # Fold ONLY sentinel / missing / identical duplicates —
                    # a same-name member carrying a DIFFERENT non-sentinel
                    # kind is a distinct (name, kind) :Object (Layer-1)
                    # that the fold must never delete (final-review P2).
                    cur = str(it.get(kind_field) or "").strip()
                    if not cur or cur.lower() == UNCLASSIFIED or cur == s2_reg:
                        warnings.append(
                            f"entity '{str(it.get('name'))[:60]}' re-typed by S4 "
                            f"(kind {it.get(kind_field)!r}) — folded into the S2 "
                            f"classifier kind {s2_reg!r} (no duplicate :Object)")
                        items[:] = [x for x in items if x is not it]
                        overrides += 1
        for it in items:
            if not isinstance(it, dict):
                continue
            s2k = register.get(f"{section}:{_identity_key(section, it)}")
            if not s2k:
                continue
            cur = str(it.get(kind_field) or "").strip()
            # Re-stamp ONLY LOST kinds (missing / the sentinel). A
            # register-matched item carrying a DIFFERENT valid non-sentinel
            # kind is a preserved distinct (name, kind) :Object — the S2
            # re-stamp must never clobber it (cycle-3 P2: the fold above
            # preserves it, so the re-stamp must too).
            if not cur or cur.lower() == UNCLASSIFIED:
                it[kind_field] = s2k
                overrides += 1
            elif cur.lower() != s2k.lower():
                ident = str(it.get("name") if section == "entities"
                            else it.get("content"))[:60]
                warnings.append(
                    f"entity '{ident}' re-typed by S4 (kind {cur!r}) — "
                    f"preserved as distinct (name, kind) :Object")
    return overrides


def _rekey_slots(embed_list: dict) -> int:
    """Slot re-key: participant slot kinds follow the classified entity
    kinds (matched by name) — a slot's kind field must agree with its
    entity's final kind. Returns the number of re-keyed slots."""
    kind_by_name: dict[str, str] = {}
    for e in embed_list.get("entities") or []:
        if isinstance(e, dict) and e.get("name") and e.get("kind"):
            k = str(e["kind"])
            if k.lower() == UNCLASSIFIED:
                # the sentinel is a terminal, never copied into a slot
                # kind; slots must reference real kinds (the write path
                # resolves the terminal on the top-level field itself)
                continue
            kind_by_name[_norm(str(e["name"]) or "")] = k
    rekeyed = 0
    for section in ("points", "events"):
        for it in embed_list.get(section) or []:
            if not isinstance(it, dict):
                continue
            slots = it.get("slots")
            if not isinstance(slots, dict):
                continue
            for role in ("subject", "object"):
                for s in slots.get(role) or []:
                    if isinstance(s, dict) and s.get("name"):
                        k = kind_by_name.get(_norm(str(s["name"]) or ""))
                        if k and str(s.get("kind")) != k:
                            s["kind"] = k
                            rekeyed += 1
    return rekeyed


# ── The orchestrator ───────────────────────────────────────────────────────

def _edus_from_conversation(conversation: list[dict]) -> list[dict]:
    return [{"index": i, "role": str(t.get("role", "unknown")),
             "text": str(t.get("content", ""))}
            for i, t in enumerate(conversation) if t.get("content")]


def extract_session_v2(model, conversation: list[dict], *, sdk=None,
                       session_id: str | None = None, chunk_size: int = 50,
                       master: dict | None = None,
                       session_date: str | None = None,
                       kind_classifier=None) -> dict:
    """The v2 production entry: conversation → S1 (chunked+compiled) → S2 →
    S3 (real-backend search) → S4 (gap review) → S5 (embed execution).

    ``kind_classifier`` (#1695 Task 5) is the injected classify-later seam:
    None + ``TORTOISE_CLASSIFY_LATER`` unset → the LEGACY pipeline — the
    classify-later machinery (classifier passes, kind-preservation re-stamp,
    slot re-key) is entirely off-path, and the flag-off renders are
    byte-identical to main. Scope of that byte-identity guarantee: the
    DEFAULT (verbose) render + the shared pipeline stages — NOT the whole
    result dict. Documented non-regressions: (a) the chain enforcer (Task 1)
    runs UNCONDITIONALLY on every arm (the A/B holds it constant) and may
    deterministically rewire about_entities on the flag-off path — its
    result key reflects that run (``items_checked`` >= 1 whenever
    about_entities exist on any arm); (b) compact mode keeps its
    pre-existing story-threaded pack selection (``_select_pack_kinds``,
    identical in main) — the byte-identity guarantee is pinned to the
    default verbose render; (c) two ADDITIVE result keys (``chain_enforcer``
    / ``classify_later``) are always present — only ``classify_later`` is
    an empty block when the flag is off.
    When set (or the env toggle is on), the stage order becomes
    S1 → S2 → classify(S2) → S3 → S4 → E4+re-stamp → classify(union,
    kind-missing only) → slot re-key → resolve_entities → post-resolution
    re-key → chain_enforcer → execute_embed, with core-only S2/S4 renders
    (pack vocabulary + chains out of the prompt).

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
    # #1746 (D1/D7): the recovery counters roll per-stage (the ladder's
    # sanitize/repair events — never error strings, never census entries).
    llm_stats: dict = {"calls": 0, "retries": 0, "truncated": 0,
                      "deadline_aborts": 0}  # #1787 P2-L: deadline-kill counter
    recovery_stats: dict[str, int] = {}
    error_census: dict[str, int] = {}
    # #1695 Task 5: the classify-later choke point — the env toggle read
    # HERE reaches every caller (sdk/hosted_api/ingest_v2/run_v2_pipeline all
    # route through extract_session_v2); an injected classifier wins.
    classify_later = kind_classifier is not None or _classify_later_enabled()
    if classify_later and kind_classifier is None:
        try:
            kind_classifier = _default_kind_classifier(model)
        except Exception as e:  # noqa: BLE001, RUF100 — never let the
            # classifier construction block capture (fail-open: legacy path)
            classify_later = False
            errors.append(f"classify-later init failed: {type(e).__name__}: {e}")
            _bump_census_class(error_census, "classify_later_init_failed")
    edus = _edus_from_conversation(conversation)
    if not edus:
        return {"session_id": session_id, "story_arc": "", "embed_list": {},
                "search": {"mode": resolve_backend_mode(), "degraded": True,
                           "reason": "no conversation content"},
                "payload": None, "chain_notes": [], "link_before_create": [],
                "supersessions": [],
                "warnings": ["empty conversation — nothing extracted"],
                "minted_kinds": [], "stats": {"llm": llm_stats,
                                                "recovery": recovery_stats},
                "errors": errors, "error_census": error_census,
                # #1695 Task 5: the evidence surfaces are always present
                # (additive keys, empty when the flag is off)
                "classify_later": {"enabled": classify_later,
                                   "s2": {}, "union": {},
                                   "restamp_overrides": 0,
                                   "slot_rekeys": 0},
                "chain_enforcer": {"notes": [], "stats": {
                    "items_checked": 0, "violations": 0,
                    "rewired": 0, "warned": 0}}}

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
                                        stats=stage_stats,
                                        master=master))
        except Exception as e:  # per-chunk failure is non-fatal
            failed_chunks += 1
            errors.append(f"S1 chunk failed: {type(e).__name__}: {e}")
            _bump_census(error_census, e)
        _rollup_llm(llm_stats, stage_stats)
        _rollup_recovery(recovery_stats, stage_stats)
    if failed_chunks:
        errors.append(f"{failed_chunks}/{len(chunks)} S1 chunks failed")
        # D1 (#1746): deterministic class for the summary line — one bump per
        # summary event, fired under the SAME condition as the append, so a
        # clean run has no stray bump.
        _bump_census_class(error_census, "s1_chunk_summary")
    story = compile_stories(chunk_stories)
    story_arc = story

    # ── S2: map to embed ───────────────────────────────────────────────────
    embed_list: dict = {}
    if story:
        stage_stats: dict = {}
        try:
            embed_list = run_s2(model, story, master,
                                session_date=session_date, edus=edus,
                                stats=stage_stats,
                                core_only=classify_later)
        except Exception as e:
            errors.append(f"S2 failed: {type(e).__name__}: {e}")
            _bump_census(error_census, e)
        # D4 (#1746): a schema-validated PARTIAL accept (truncated tail
        # dropped) is a recorded ERROR — the embed list is incomplete
        # (valid=false), never a clean outcome. The partial list IS used.
        if stage_stats.get("partial"):
            errors.append("S2 output partial — truncated tail dropped "
                          "(embed list incomplete)")
            _bump_census_class(error_census, "partial_parse")
        _rollup_llm(llm_stats, stage_stats)
        _rollup_recovery(recovery_stats, stage_stats)

    # ── classify(S2) (#1695 Task 5): the first classify pass — the pack-
    # domain items the core-only S2 emitted as "unclassified" get their
    # kinds BEFORE S3 so the graph search + S4 see final kinds (typed refs
    # guaranteed pre-resolution). Fail-open: never blocks capture.
    s2_classify_stats: dict = {}
    s2_classify_warnings: list[str] = []
    s2_assignments: dict = {}
    if classify_later and embed_list:
        try:
            items = _collect_classify_items(embed_list)
            if items:
                out = kind_classifier.classify_items(items)
                _apply_classify_kinds(embed_list, out["assignments"])
                s2_classify_stats = out["stats"]
                s2_classify_warnings = out["warnings"]
                s2_assignments = out["assignments"]
                _bump_classify_census(error_census, out["stats"])
                # The adjudication tail's LLM spend (calls/retries/truncated)
                # rolls into the per-session llm_stats — the A/B cost gate
                # must see the flag-on arm's batched adjudication cost.
                usage = out["stats"].get("llm")
                if usage:
                    _rollup_llm(llm_stats, usage)
        except Exception as e:  # never block capture (P1)
            errors.append(f"classify(S2) failed: {type(e).__name__}: {e}")
            _bump_census_class(error_census, "classify_error")

    # ── S3: search the graph (real backend, graceful degradation) ──────────
    search = search_graph(sdk, embed_list, story)

    # ── S4: review gaps → complete embed list (E4: merges-not-replaces) ───
    complete_list: dict = embed_list
    s4_warnings: list[str] = []
    s4_merge_stats: dict = {}
    if story:
        stage_stats: dict = {}
        try:
            s4 = run_s4(model, story, search, embed_list, master,
                        session_date=session_date, edus=edus,
                        stats=stage_stats,
                        core_only=classify_later)
            if s4 and (s4.get("entities") or s4.get("points") or
                       s4.get("events") or s4.get("operators")):
                complete_list = merge_embed_lists(embed_list, s4)
                s4_merge_stats = _s4_merge_stats(embed_list, s4, complete_list)
            else:
                # graceful degradation — S2 output stands; not an error
                s4_warnings.append("S4 returned an empty list — kept S2 output")
        except Exception as e:
            errors.append(f"S4 failed: {type(e).__name__}: {e} — kept S2 output")
            _bump_census(error_census, e)
        # D4 (#1746): a schema-validated PARTIAL S4 accept is a recorded
        # ERROR (same contract as the S2 partial above) — the partial IS
        # merged over the S2 base (merge_embed_lists preserves S2 intact).
        if stage_stats.get("partial"):
            errors.append("S4 output partial — truncated tail dropped "
                          "(embed list incomplete)")
            _bump_census_class(error_census, "partial_parse")
        _rollup_llm(llm_stats, stage_stats)
        _rollup_recovery(recovery_stats, stage_stats)

    # ── classify-later post-merge pass (#1695 Task 5): E4 + kind-preservation
    # re-stamp → classify(union, kind-missing only) → slot re-key. The S2
    # classifier kinds survive S4 re-emission (re-stamp + duplicate fold),
    # the union's untyped gaps get kinds, and participant-slot kinds follow
    # the final entity kinds (typed refs guaranteed pre-resolution).
    s2_kind_register: dict = {}
    union_classify_stats: dict = {}
    union_classify_warnings: list[str] = []
    restamp_overrides = 0
    slot_rekeys = 0
    if classify_later and complete_list:
        s2_kind_register = _s2_kind_register(embed_list, s2_assignments)
        try:
            restamp_warnings: list[str] = []
            restamp_overrides = _restamp_s2_kinds(complete_list,
                                                  s2_kind_register,
                                                  restamp_warnings)
            if restamp_warnings:
                s4_warnings.extend(restamp_warnings)
            items = _collect_classify_items(complete_list)
            if items:
                out = kind_classifier.classify_items(items)
                _apply_classify_kinds(complete_list, out["assignments"])
                union_classify_stats = out["stats"]
                union_classify_warnings = out["warnings"]
                _bump_classify_census(error_census, out["stats"])
                # roll the adjudication tail spend (same as the S2 pass).
                usage = out["stats"].get("llm")
                if usage:
                    _rollup_llm(llm_stats, usage)
            slot_rekeys = _rekey_slots(complete_list)
        except Exception as e:  # never block capture (P1)
            errors.append(f"classify(union) failed: {type(e).__name__}: {e}")
            _bump_census_class(error_census, "classify_error")

    # ── entity resolution (D3): deterministic-first, bounded LLM fallback.
    # Runs BETWEEN S4 and S5; rewrites the embed list's entity names +
    # about_entities/slot refs to canonical existing names so execute_embed's
    # link-before-create + the server-side aboutObject MERGE-by-name land on
    # the canonical Object. Skipped when S3 is degraded or has no candidates
    # (nothing to resolve against). Never blocks capture (P1).
    resolution_records: list[dict] = []
    resolution_warnings: list[str] = []
    if search and not search.get("degraded") and (search.get("entities") or []):
        ent_refs = [{"name": str(e.get("name", "")).strip(),
                     "kind": str(e.get("kind", "")).strip()}
                    for e in (complete_list.get("entities") or [])
                    if isinstance(e, dict) and e.get("name")]
        if ent_refs:
            try:
                res = resolve_entities(ent_refs, search, model=model)
                if res.get("map"):
                    complete_list = _apply_entity_resolution(
                        complete_list, res["map"])
                resolution_records = res.get("records") or []
                resolution_warnings = res.get("warnings") or []
                # #1695 Task 5: post-resolution re-key — the resolution may
                # rename entities; the participant-slot kinds are re-applied
                # (idempotent — kinds don't change, names may) so the typed
                # refs stay consistent with the canonical entities.
                if classify_later:
                    slot_rekeys += _rekey_slots(complete_list)
            except Exception as e:  # noqa: BLE001, RUF100 — resolve_entities is
                # guarded internally, but the orchestrator never dies on
                # resolution (P1: degrade to phase-1/ADD semantics)
                errors.append(f"entity resolution failed: {type(e).__name__}: {e}")
                # D1 (#1746): deterministic class for the previously-
                # uncensused resolution failure path.
                _bump_census_class(error_census, "entity_resolution_failed")

    # ── chain enforcement (#1695 Task 1): DETERMINISTIC rewire between the
    # resolution pass and S5 — the prompts' advisory "TRY TO REPAIR" becomes
    # guaranteed. Never-invent / never-drop: reverse-chain about_entities
    # pairs rewire through the nearest valid chain intermediate ONLY when
    # unambiguous; else warn-and-keep. Runs on EVERY arm (independent of the
    # classify-later flag — the A/B holds it constant). Never blocks capture.
    chain_enforcer_notes: list[dict] = []
    chain_enforcer_stats: dict = {}
    try:
        from tortoise.chain_enforcer import validate_and_rewire
        complete_list, chain_enforcer_notes, chain_enforcer_stats = \
            validate_and_rewire(complete_list, master)
    except Exception as e:  # noqa: BLE001, RUF100 — never block capture (P1)
        errors.append(f"chain enforcement failed: {type(e).__name__}: {e}")
        # D1 (#1746): deterministic class for the previously-uncensused
        # chain-enforcement failure path.
        _bump_census_class(error_census, "chain_enforcement_failed")

    # ── chain completeness (#1933, E2E-4 negative a): DETERMINISTIC check
    # on the embed list (PRE-repair — pack point kinds like
    # agent-ops:rationale are still visible here, unlike the commit payload
    # where FIX P repairs them to statement). A pack chain whose first
    # emitted step is missing its next step (ruleLifecycle: rule without
    # rationale) is an incomplete chain — warn per the chain's manifest
    # enforcement, never block. Notes are collected NOW and merged into
    # ``result`` after it exists (the chain-enforcement block runs before
    # ``result`` is created; the S5-failure branch carries them too).
    chain_completeness_notes: list[dict] = []
    try:
        chain_completeness_notes = validate_chain_completeness(
            complete_list, master)
    except Exception as e:  # noqa: BLE001, RUF100 — never block capture (P1)
        errors.append(f"chain completeness check failed: {type(e).__name__}: {e}")
        _bump_census_class(error_census, "chain_completeness_failed")

    # ── S5: embed execution (deterministic) ────────────────────────────────
    if not complete_list:
        errors.append("no embed list produced (S2/S4 empty) — nothing to embed")
        # D1 (#1746): deterministic class for the previously-uncensused
        # empty-embed-list path.
        _bump_census_class(error_census, "empty_embed_list")
    try:
        result = execute_embed(complete_list, search, session_id=session_id,
                               story_arc=story_arc, master=master,
                               session_date=session_date, edus=edus)
    except Exception as e:  # S5 must NEVER block the pipeline (design §7.4)
        errors.append(f"S5 failed: {type(e).__name__}: {e}")
        # D1 (#1746): deterministic class for the previously-uncensused S5
        # failure path.
        _bump_census_class(error_census, "s5_failed")
        result = {"payload": None, "chain_notes": [], "link_before_create": [],
                  "supersessions": [], "noops": [], "deletions": [],
                  "warnings": [f"S5 embed execution failed: {e}"],
                  "minted_kinds": [], "stats": {}}
    result["chain_enforcer"] = {      # #1695 Task 1 evidence surface
        "notes": chain_enforcer_notes,
        "stats": chain_enforcer_stats,
    }
    # #1933 (E2E-4 negative a): the chain-completeness evidence surface +
    # human-readable warnings (additive keys — the S5-failure branch above
    # carries the same merge).
    result["chain_completeness"] = {"notes": chain_completeness_notes}
    if chain_completeness_notes:
        result.setdefault("warnings", []).extend(
            f"{n['finding']}" for n in chain_completeness_notes)
    # #1695 Task 5: the classify-later evidence surface (flag-off: the
    # empty block — no telemetry growth, additive keys only).
    result["classify_later"] = {
        "enabled": classify_later,
        "s2": s2_classify_stats,
        "union": union_classify_stats,
        "restamp_overrides": restamp_overrides,
        "slot_rekeys": slot_rekeys,
    }
    if classify_later:
        # the unclassified terminal is resolved at write (execute_embed's
        # sentinel repair) — count it in the census. The UNION pass
        # re-classifies the same below-floor S2 survivors (they are still
        # kind-missing in the merged list), so its count is authoritative;
        # fall back to the S2 count only when the union pass never ran.
        # union_classify_stats is non-empty ONLY when the union pass ran
        # (collected items) — gate the fallback on that, not on the count
        union_u = union_classify_stats.get("unclassified") or 0
        terminal = (union_u if union_classify_stats
                    else (s2_classify_stats.get("unclassified") or 0))
        if terminal:
            error_census["unclassified_terminal"] = \
                error_census.get("unclassified_terminal", 0) + terminal
        for w in s2_classify_warnings + union_classify_warnings:
            result.setdefault("warnings", []).append(w)
    # The enforcer's notes are authoritative for every violation it examined
    # (backstop note ⟹ enforcer note — both scan the same item/chain
    # subsequences). When it ruled anything, its notes + the model's OWN
    # chain_notes (from the embed list) replace the backstop's advisory
    # notes, which would otherwise duplicate/contradict (a warned-and-kept
    # pair gets re-flagged by validate_chains with its weaker recommendation).
    if chain_enforcer_notes:
        llm_chain_notes = [dict(c) for c in (complete_list.get("chain_notes") or [])
                           if isinstance(c, dict)]
        result["chain_notes"] = chain_enforcer_notes + llm_chain_notes
    result["session_id"] = session_id
    result["story_arc"] = story_arc
    result["embed_list"] = complete_list
    result["s2_embed"] = embed_list          # S2 raw (pre-S4) — owner loop
    result["search"] = search
    result["errors"] = errors
    if resolution_records:
        result["resolution"] = resolution_records   # D3 evidence surface
        result["link_before_create"] = (
            (result.get("link_before_create") or []) + [
                {"searched_for": f"entity '{r['name']}'", "found": True,
                 "note": f"resolved via entity resolution ({r['mode']}) to "
                         f"{r['resolves_to']}"}
                for r in resolution_records])
    if resolution_warnings:
        result["warnings"] = resolution_warnings + (result.get("warnings") or [])
    if s4_warnings:
        result["warnings"] = s4_warnings + (result.get("warnings") or [])
    result["stats"]["elapsed_s"] = round(time.time() - t0, 1)
    result["stats"]["chunks"] = len(chunks)
    result["stats"]["failed_chunks"] = failed_chunks
    result["stats"]["s4_merge"] = s4_merge_stats  # E4 (#1536): no-silent-drop proof
    # M3 (#1524, D3): additive integrity surface — the per-session census +
    # LLM roll-up feed the harness's per-question ``valid`` / ``error_classes``
    # (M4). The payload telemetry's hardcoded retry_count is wired to the
    # real value (previously always 0).
    result["error_census"] = error_census
    result["stats"]["llm"] = llm_stats
    result["stats"]["recovery"] = recovery_stats  # #1746 (D7): ladder events
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

# #1787 Task 5 Step 0 (P2-L): lock-guarded deadline-abort counter — the
# increment happens at the ``_call_once`` deadline-kill raise (the one seam
# distinct from a network-transport TimeoutError); the guard makes the
# counter safe even when a SHARED stats dict is written from multiple
# worker threads (LOAD/INPLACE_ADD/STORE loses updates without it).
_DEADLINE_ABORTS_LOCK = threading.Lock()

# Bounded generations (D2): stage caps — S1 narrative is small (1500); the
# S2/S4 embed JSON must clear the 4000-token truncation floor. #1787: the
# 8000 default was set in M3 (#1524) against the V3-era 8K model ceiling;
# deepseek-v4 models allow 384K max output, and 15/6720 reval calls hit
# the old cap (silent tail-entity loss via partial-accept) — raise the
# default to 16000 (still ≪ 384K ceiling; env override remains the
# mechanical lever). The single env override TORTOISE_EXTRACTOR_MAX_TOKENS
# (int, read at call time) raises BOTH stages without a code change — the
# retry-then-fix protocol's mechanical lever (D6: ``transient_timeout``
# spike → raise the cap).
_S1_MAX_TOKENS = 1500
_S2_S4_MAX_TOKENS = 16000


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
    """S2/S4 output that fails the parse-recovery ladder — census class
    ``parse_error`` / ``truncated_parse_error`` (D2, #1746).

    Carries the class-decision signal: ``truncated`` = the FIRST
    parse-failing attempt's ``finish_reason`` was ``"length"`` (the
    truncation hypothesis — H3) vs ``parse_error`` (contamination /
    sloppiness — H2); ``attempt`` = which completion attempt failed;
    ``excerpt`` = a bounded error region for the error-informed re-prompt
    (D3)."""

    def __init__(self, message: str, *, truncated: bool = False,
                 attempt: int = 0, excerpt: str = ""):
        super().__init__(message)
        self.truncated = truncated
        self.attempt = attempt
        self.excerpt = excerpt


def _bump_census(error_census: dict[str, int], e: BaseException) -> None:
    """Record one stage-failure in the per-session census (D3, #1746).

    ``_ParseError`` is class-decided on its ``truncated`` flag (D2):
    ``truncated_parse_error`` vs ``parse_error``; every other exception
    keeps ``_classify_error`` (P2 delegation preserved)."""
    if isinstance(e, _ParseError):
        cls = "truncated_parse_error" if e.truncated else "parse_error"
    else:
        cls = _classify_error(e)
    error_census[cls] = error_census.get(cls, 0) + 1


def _bump_census_class(error_census: dict[str, int], cls: str) -> None:
    """Class-explicit census bump (D1, #1746) — the deterministic-append
    rule: every ``errors.append`` site in ``extract_session_v2`` pairs
    exactly ONE census class, so ``len(errors) == sum(error_census)`` holds
    structurally for the raise-paired classes (criterion 2; the pilot's
    16-vs-14 drift is gone).

    #1695 Task 5 — DUAL-provenance classes (documented, flag-only):
    ``classify_error`` is BOTH raise-paired (the classify(S2)/classify(union)
    exception handlers append an ``errors`` entry AND bump this class) AND
    additively counted per-item by ``_bump_classify_census`` (the
    classifier's fail-open per-item counters — rerank/adjudication/retrieval
    failures never raise, so no ``errors`` entry pairs THOSE bumps).
    ``embedding_error`` / ``unclassified_terminal`` are ADDITIVE ONLY (no
    ``errors`` entry pairs them). The structural len-equality holds on the
    raise-paired classes; the flag-only classes are governed by their own
    A/B thresholds (parse-census equality computes on the class
    INTERSECTION, per the plan)."""
    error_census[cls] = error_census.get(cls, 0) + 1


def _bump_classify_census(error_census: dict[str, int], stats: dict) -> None:
    """#1695 Task 5: wire the classifier's fail-open counters into the
    per-session error census (``classify_error`` / ``embedding_error`` — the
    flag-only classes, additive observability per the documented exception
    in ``_bump_census_class``; never paired with an ``errors`` entry)."""
    for cls, key in (("classify_error", "classify_errors"),
                     ("embedding_error", "embedding_errors")):
        n = stats.get(key) or 0
        if n:
            error_census[cls] = error_census.get(cls, 0) + n


def _rollup_llm(llm_stats: dict, stage_stats: dict) -> None:
    """Roll one stage's per-call stats into the per-session LLM roll-up
    (D3: stats['llm'] = calls / retries / truncated across S1/S2/S4; #1787
    Task 5 Step 0 P2-L: ``deadline_aborts`` — deadline-killed generations
    are billed but never counted by any token accumulator, so the harness
    bounds the loss via this counter)."""
    llm_stats["calls"] += stage_stats.get("attempts", 0)
    llm_stats["retries"] += stage_stats.get("retries", 0)
    llm_stats["truncated"] += int(bool(stage_stats.get("truncated")))
    llm_stats["deadline_aborts"] += stage_stats.get("deadline_aborts", 0)


def _rollup_recovery(recovery_stats: dict, stage_stats: dict) -> None:
    """Roll one stage's recovery counters into the per-session recovery
    roll-up (#1746, D4/D7: the ladder's sanitize / sanitize_insufficient /
    repair events — warning-only, never error strings, never census)."""
    for k, v in (stage_stats.get("recovery") or {}).items():
        recovery_stats[k] = recovery_stats.get(k, 0) + v


def _call_once(model, system: str, user: str, *, deadline_s: int,
               max_tokens: int | None, stats: dict | None) -> tuple[str | None, object | None]:
    """One wall-clock-bounded completion attempt (M3 D1: each retry attempt
    gets its OWN deadline — a wedged call cannot stay wedged across retries).

    Returns ``(resp, finish_reason)`` — the finish reason captured in the
    calling thread right after ``complete()`` returns (F4 #1780).

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
            # F4 (#1780): capture the finish reason in the SAME thread as
            # the call (happens-before via the join below). Reading
            # ``model.last_finish_reason`` later from the caller thread is a
            # cross-thread race under ``--workers > 1``: another thread's
            # complete() can overwrite the shared attribute between the
            # return and the read.
            box["finish_reason"] = getattr(model, "last_finish_reason", None)
        except BaseException as e:  # noqa: BLE001, RUF100
            box["exc"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=deadline_s)
    if t.is_alive():
        # Pilot #1549: kill the hung request so the daemon thread's blocking
        # socket read raises and dies (was: abandoned thread leaked the
        # socket + kept billing; the API stalls mid-chunked-response and a
        # trickle defeats the requests read timeout). close() is best-effort.
        close = getattr(model, "close", None)
        if close is not None:
            try:  # noqa: SIM105
                close()
            except Exception:
                pass
        # #1787 Task 5 Step 0 (P2-L/P1-E): count the deadline kill — the
        # abandoned daemon thread keeps running and the provider keeps
        # billing, and the spend is invisible to any token accumulator. The
        # counter lives on the per-call stats dict (rolled into the session
        # llm roll-up by _rollup_llm) and is lock-guarded so even a SHARED
        # stats dict across worker threads never loses an increment.
        if stats is not None:
            with _DEADLINE_ABORTS_LOCK:
                stats["deadline_aborts"] = stats.get("deadline_aborts", 0) + 1
        raise TimeoutError(f"model call exceeded {deadline_s}s")
    if "exc" in box:
        raise box["exc"]
    return (box.get("resp"), box.get("finish_reason"))


def _scaled_deadline(base: int, max_tokens: int | None) -> int:
    """Scale a base deadline with the generation budget (#1787 Task 2 Step 6).

    A worst-case 16K emission at the conservative 20-25 tok/s floor needs
    ~640-800s — beyond the historical 600s default (8K ≈ 320s cleared it).
    The multiplier is **0.05 s/token** (cycle-4 P2-K decision: the old 0.04
    multiplier put the scaled deadline EXACTLY at the 25 tok/s emission
    point — zero margin, killing ~20-24 tok/s stragglers the old 8K/600s
    would have completed; 0.05 → 800s at 16K restores a 25% margin at
    25 tok/s and covers down to ~20 tok/s). The ``(max_tokens or 0)`` guard
    is REQUIRED — callers pass ``max_tokens=None`` when no cap applies."""
    return max(base, int(0.05 * (max_tokens or 0)))


def _complete(model, system: str, user: str, *, deadline_s: int | None = None,
              max_tokens: int | None = None, retries: int | None = None,
              backoff_base: float | None = None,
              backoff_cap: float | None = None,
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
    0 = documented uncapped escape hatch). ``deadline_s`` defaults to None →
    the scaled default ``_scaled_deadline(600, max_tokens)`` (#1787 Task 2
    Step 6: a worst-case 16K emission needs 800s, beyond the old fixed 600s;
    the None sentinel keeps explicit-deadline callers unchanged — an
    explicit ``deadline_s`` always wins, never ``max()``-ed). ``stats``
    (optional) records attempts / retries / truncated / last_class per call
    for the per-session LLM roll-up (D3), plus ``deadline_aborts`` (#1787
    P2-L) on a deadline kill.

    ``retries``/``backoff_*`` default to None → the module constants
    (``_COMPLETE_RETRIES`` / ``_BACKOFF_BASE_S`` / ``_BACKOFF_CAP_S``) are
    read at CALL time (live constants — the test hook pins them to zero/small
    values so the fail path never sleeps; cycle-3 P2 test hygiene).

    Total worst-case wall clock = attempts × deadline_s (documented — the
    abandoned daemon thread after a deadline keeps running and the provider
    keeps billing; accepted, bounded per attempt and counted via
    ``deadline_aborts``)."""
    if retries is None:
        retries = _COMPLETE_RETRIES
    if backoff_base is None:
        backoff_base = _BACKOFF_BASE_S
    if backoff_cap is None:
        backoff_cap = _BACKOFF_CAP_S
    if deadline_s is None:
        deadline_s = _scaled_deadline(600, max_tokens)
    for attempt in range(1, retries + 2):
        try:
            resp, finish_reason = _call_once(model, system, user,
                                             deadline_s=deadline_s,
                                             max_tokens=max_tokens,
                                             stats=stats)
            truncated = finish_reason == "length"
            if stats is not None:
                stats.update(attempts=attempt, retries=attempt - 1,
                             last_class=None, truncated=bool(truncated),
                             finish_reason=finish_reason)
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
    """Robust JSON extraction (pilot #1549 research).

    The model intermittently wraps the embed list in markdown code fences
    (```json ... ```) and/or truncates it (finish_reason == "length") —
    the v2-era strict regex ``{.*}`` then reported "no JSON block in
    output" even for perfect JSON, and the parse-retry re-prompted into
    the same failure (the 666-parse_error census). This version:
    (1) strips markdown fences, (2) finds the JSON object by
    brace-balancing from the first '{', tolerating a truncated tail
    (no final closing brace), (3) recovers trailing junk with the
    progressive tail-cut. Raises ``ValueError`` only when no JSON can be
    extracted at all — the caller's parse-retry still applies."""
    resp = (response or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", resp, re.S)
    if m:
        resp = m.group(1).strip()
    start = resp.find("{")
    if start < 0:
        raise ValueError("no JSON block in output")
    depth = 0
    in_str = False
    esc = False
    end = len(resp)
    for i in range(start, len(resp)):
        c = resp[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    block = resp[start:end]
    for cut in (None, -1, -2, -3, -5, -10, -20):
        try:
            return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError:
            continue
    raise ValueError("unparseable JSON")


# ══ #1746 (D4/D5): the parse-boundary recovery ladder ════════════════════
#
# ``_parse_json_robust`` replaces the binary parse-or-fail at the S2/S4 seam:
# rung 1 canonical → rung 2 sanitize (H2 control-char contamination) → rung 3
# bounded repair → rung 4 schema-validated partial-accept (H3 truncation) →
# rung 5 raise. Every recovery is a recorded event (``stats["recovery"]`` /
# ``stats["partial"]``); failures keep their mechanism class via
# ``_ParseError.truncated`` (D2).

#: D5: the structural output-shape schema, hand-derived from OUTPUT_CONTRACT
#: (kept adjacent — a contract edit → schema edit is a NEW coupling, tracked
#: in the plan's Open questions). Each present section must be a LIST of
#: dicts; each item must carry its required keys (primitive-typed); unknown
#: keys and empty arrays ride through (valid).
_OUTPUT_SCHEMA: dict[str, tuple[str, ...]] = {
    "entities": ("name", "kind"),
    "events": ("content", "eventKind"),
    "points": ("content",),
    "operators": ("src", "dst", "op_type"),
    "chain_notes": ("chain", "finding", "action"),
    "link_before_create": ("searched_for", "found"),
    "retractions": ("content", "id"),  # content | id — either satisfies
}

#: D5: primitive types for the required keys (structural strictness only).
_SCHEMA_PRIMITIVES: dict[str, tuple[type, ...]] = {
    "name": (str,), "kind": (str,), "content": (str,),
    "eventKind": (str,), "src": (str,), "dst": (str,),
    "op_type": (str,), "chain": (str,), "finding": (str,),
    "action": (str,), "searched_for": (str,), "found": (bool,),
    "id": (str,),
}


def _is_primitive(v) -> bool:
    return not isinstance(v, (dict, list, tuple)) and v is not None


def _validate_output_shape(parsed) -> tuple[bool, list[str]]:
    """D5 (#1746): structural-only output-shape validation — a schema gate
    for the ladder's rungs 3-4 (a mis-tracked sanitize/repair scan can never
    corrupt: junk output fails schema and falls through). Top-level must be
    a dict; each PRESENT contract section must be a LIST of dicts; each item
    must carry its required keys with primitive values; unknown keys and
    empty arrays are VALID (fields ride through by reference — S5's
    execution validation owns semantic repair). ``retractions`` items need
    ``content`` OR ``id`` (the contract's ``content|id``)."""
    issues: list[str] = []
    if not isinstance(parsed, dict):
        return False, [f"top-level output is {type(parsed).__name__}, not an object"]
    for section, required in _OUTPUT_SCHEMA.items():
        if section not in parsed:
            continue
        items = parsed[section]
        if not isinstance(items, list):
            issues.append(f"section {section!r} is {type(items).__name__}, not a list")
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                issues.append(f"{section}[{idx}] is {type(item).__name__}, not an object")
                continue
            if section == "retractions" and not (
                    "content" in item or "id" in item):
                issues.append(f"retractions[{idx}] needs content or id")
            for key in required:
                if key not in item:
                    if section == "retractions":
                        continue  # content | id — the disjunction is above
                    issues.append(f"{section}[{idx}] missing required key {key!r}")
                    continue
                if not _is_primitive(item[key]):
                    issues.append(
                        f"{section}[{idx}].{key} is a non-primitive value")
                    continue
                expect = _SCHEMA_PRIMITIVES.get(key)
                if expect and not isinstance(item[key], expect):
                    issues.append(
                        f"{section}[{idx}].{key} has the wrong type "
                        f"(expected {expect[0].__name__})")
    return (not issues), issues


#: C0 control chars (0x00-0x1F) — escaped inside string literals by the
#: sanitize rung (H2 output-side contamination, #1746 D4).
_C0_CONTROL = frozenset(chr(c) for c in range(0x20))


_SANITIZE_ESCAPES = {
    "\n": "\\n", "\t": "\\t", "\r": "\\r",
    "\b": "\\b", "\f": "\\f", "\"": "\\\"", "\\": "\\\\",
}


def _sanitize_control_chars(response: str) -> str:
    """D4 rung 2 (#1746): string-aware scan — escape raw C0 control chars
    (0x00-0x1F, incl. raw newlines/tabs) INSIDE string literals as their
    JSON escapes; structural whitespace is untouched. Returns the original
    string unchanged when nothing was altered (so callers can detect
    whether the rung did work)."""
    out: list[str] = []
    in_str = False
    esc = False
    changed = False
    for c in response:
        if in_str:
            if esc:
                out.append(c)  # part of an escape sequence — leave as-is
                esc = False
                continue
            if c == "\\":
                out.append(c)
                esc = True
                continue
            if c == '"':
                in_str = False
                out.append(c)
                continue
            if c in _C0_CONTROL:
                out.append(_SANITIZE_ESCAPES.get(c, f"\\u{ord(c):04x}"))
                changed = True
                continue
            out.append(c)
            continue
        if c == '"':
            in_str = True
        out.append(c)
    return "".join(out) if changed else response


#: D4 rung 3: bounded missing-comma repairs at boundary joins (first-valid-
#: wins; the schema gate backstops a mis-targeted substitution).
_REPAIR_RULES: tuple[tuple[str, str], ...] = (
    ('}"{', '}", "{'),
    (']"{"', ']", "{'),
    ('}"[', '}", "['),
    (']"["', ']", "['),
    ('}{', '},{'),
    ('][', '],['),
    ('}"', '},"'),  # dict value object → next key (bounded, gated)
)


def _apply_repair_rule(working: str, find: str, repl: str) -> str | None:
    """String-aware first-match application of one comma-insertion rule:
    find the FIRST occurrence of ``find`` OUTSIDE any string literal
    (in_str/esc tracker) and replace it; None when no outside-string
    occurrence exists (a rule that only fires inside strings never
    corrupts)."""
    in_str = False
    esc = False
    n = len(find)
    i = 0
    while i <= len(working) - n:
        c = working[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if working.startswith(find, i):
            return working[:i] + repl + working[i + n:]
        i += 1
    return None


def _repair_candidates(working: str) -> list[str]:
    """D4 rung 3 (#1746): bounded CONTENT-PRESERVING repair candidates on
    the working text — (a) unterminated object → append up to 8 closers;
    (b) the bounded missing-comma rule list, applied string-aware
    (``_apply_repair_rule``: a rule that only fires inside a string value
    is never applied — no silent in-string corruption). NO data-dropping
    tail-cuts here (a truncation cut at an item boundary is a recorded
    ERROR via rung 4's partial-accept — ``stats["partial"]``, never a
    warning-only "repair"). First-valid-wins + schema gate keep it safe;
    no free-form repair library, no unbounded heuristics."""
    candidates: list[str] = []
    for k in range(1, 9):
        candidates.append(working + "}" * k)
    for find, repl in _REPAIR_RULES:
        r = _apply_repair_rule(working, find, repl)
        if r is not None:
            candidates.append(r)
    return candidates


_EMBED_SECTIONS = ("entities", "points", "events", "operators")


def _close_balanced(text: str) -> str | None:
    """D4 rung 4 helper (#1746): append the minimal closing brackets that
    make a JSON prefix balanced (the truncation cut lands mid-structure, so
    a strict ``json.loads(prefix)`` can never be valid — the recovered
    prefix is CLOSED before parsing). Returns None when the prefix cannot
    be closed safely (an unterminated string/escape at the cut, or a
    mismatched close — a mis-tracked cut must never corrupt)."""
    stack: list[str] = []
    in_str = False
    esc = False
    for c in text:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "[{":
            stack.append("]" if c == "[" else "}")
        elif c in "]}":
            if not stack:
                return None
            if stack[-1] != ("]" if c == "]" else "}"):
                return None
            stack.pop()
    if in_str or esc:
        return None
    return text + "".join(reversed(stack))


def _longest_valid_prefix(text: str) -> dict | None:
    """D4 rung 4 (#1746): schema-validated partial-accept — progressive
    prefix cuts at item boundaries (``}``/``]`` positions from the tail,
    bounded ≤ 200 candidates), each closed to balance (``_close_balanced``)
    and parsed; the LONGEST prefix that parses AND passes the schema gate
    with ≥ 1 non-empty embed section (entities/points/events/operators —
    mirroring the S4 caller's merge condition, so a partial the caller
    would discard can never be falsely classed ``partial_parse``) is
    accepted. A truncated-to-empty prefix never counts; a prefix with only
    chain_notes/link_before_create/retractions non-empty falls through
    (returns None → the caller raises)."""
    positions = sorted((i for i, c in enumerate(text) if c in "}]"),
                       reverse=True)
    for i in positions[:200]:
        closed = _close_balanced(text[:i + 1])
        if closed is None:
            continue
        try:
            parsed = json.loads(closed)
        except json.JSONDecodeError:
            continue
        ok, _ = _validate_output_shape(parsed)
        if not ok:
            continue
        if not any(parsed.get(s) for s in _EMBED_SECTIONS):
            continue
        return parsed
    return None


def _parse_json_robust(response: str, *, stats: dict | None = None) -> dict:
    """D4 (#1746): the parse-boundary recovery ladder — raises
    ``_ParseError`` on final failure.

    Rungs per attempt: 1 canonical ``_parse_json``; 2 string-aware sanitize
    (raw C0 control chars inside string literals → JSON escapes; success →
    ``stats["recovery"]["sanitize"] += 1``; altered-but-unparseable → the
    sanitized text becomes the ``working`` input for rungs 3-4 +
    ``sanitize_insufficient += 1``); 3 bounded repair on ``working``
    (schema-gated, first-valid-wins; success → ``recovery["repair"] += 1``);
    4 schema-validated partial-accept on ``working`` (success →
    ``stats["partial"] = True`` — the caller appends the ``partial_parse``
    error class); 5 raise. The D5 schema gate backstops a mis-tracked scan
    before any rung-3/4 output is accepted — worst case it fails schema and
    falls through, never corrupting."""
    response = response or ""  # a None adapter response (null provider
    # content) must not crash rung 2's ``_sanitize_control_chars`` scan
    # (the ``_error_excerpt`` docstring already claims None tolerance).
    last_err: ValueError | None = None
    # rung 1 — canonical (fences, brace-balance, tail-cuts)
    try:
        return _parse_json(response)
    except ValueError as e:
        last_err = e
    recovery = (stats.setdefault("recovery", {})
                if stats is not None else None)
    # rung 2 — sanitize (H2 output-side contamination)
    sanitized = _sanitize_control_chars(response)
    altered = sanitized != response
    if altered:
        try:
            parsed = _parse_json(sanitized)
            if recovery is not None:
                recovery["sanitize"] = recovery.get("sanitize", 0) + 1
            return parsed
        except ValueError as e:
            last_err = e
            if recovery is not None:
                recovery["sanitize_insufficient"] = (
                    recovery.get("sanitize_insufficient", 0) + 1)
    working = sanitized if altered else response
    # rung 3 — bounded repair (schema-gated, first-valid-wins)
    for candidate in _repair_candidates(working):
        try:
            parsed = _parse_json(candidate)
        except ValueError as e:
            last_err = e
            continue
        ok, _ = _validate_output_shape(parsed)
        if ok:
            if recovery is not None:
                recovery["repair"] = recovery.get("repair", 0) + 1
            return parsed
    # rung 4 — schema-validated partial-accept (H3 truncation)
    prefix = _longest_valid_prefix(working)
    if prefix is not None:
        if stats is not None:
            stats["partial"] = True
        return prefix
    # rung 5 — raise (the deepest failure's message + bounded excerpt)
    raise _ParseError(str(last_err or ValueError("unparseable JSON")),
                      excerpt=_error_excerpt(response, last_err))


__all__ = [  # noqa: RUF022
    "build_master_list", "master_kind_forms",
    "run_s1", "chunk_transcript", "compile_stories",
    "run_s2", "render_s2_prompt",
    "resolve_backend_mode", "search_graph",
    "run_s4", "render_s4_prompt",
    "merge_embed_lists", "_merge_key", "_s4_merge_stats",
    "execute_embed", "validate_chains", "validate_chain_completeness",
    "derive_supersessions",
    "classify_consolidation", "DecisionRecord", "resolve_entities",
    "extract_session_v2",
    "S1_TMPL", "S2_TMPL", "S4_TMPL", "OUTPUT_CONTRACT",
    "S2_TMPL_CORE_ONLY", "S4_TMPL_CORE_ONLY", "OUTPUT_CONTRACT_CORE_ONLY",
    "SUBJECTS", "EVENTS", "CHAINS",
    "UNCLASSIFIED",
]
