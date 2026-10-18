"""tortoise_analyze — template-based NL→Cypher graph analysis.

Single MCP tool: tortoise_analyze(question) → natural language answer.
Template Cypher + LLM for intent classification and result synthesis.
No LLM-Cypher generation (rejected: 61% accuracy, 40% silent errors).
"""
from __future__ import annotations  # noqa: I001

import json, os, re  # noqa: E401
from typing import Any

from .live import (
    _live_only,
    _terminal_excluded,
    epistemic_disjunction,
    epistemic_labels_of,
    epistemic_sweep,
)


# ═══════════════════════════════════════════════════════════════════
# Template Registry
# ═══════════════════════════════════════════════════════════════════

TEMPLATES: dict[str, dict] = {
    "disagreement": {
        "triggers": ["disagree", "conflict", "oppose", "contradict", "tension", "contested"],
        "description": "Find NAND conflicts between high-confidence claims",
        "subgraph_vars": ["a", "b"],
        "cypher": """
            MATCH (a:Point)<-[:NAND]-(op:Point {op_type:\"NAND\"})-[:NAND]->(b:Point)
            WHERE a.id <> b.id
              AND a.is_operator = false
              AND b.is_operator = false
              AND coalesce(a.confidence, 0.5) > 0.45
              AND coalesce(b.confidence, 0.5) > 0.45
            RETURN a.id, a.content, coalesce(a.confidence,0.5) as a_conf,
                   b.id, b.content, coalesce(b.confidence,0.5) as b_conf
            ORDER BY a_conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_pairs(rows, "conflict"),
    },
    "strongest_for": {
        "triggers": ["strongest", "best argument", "most support", "evidence for", "supports"],
        "description": "Find claims supporting a target via IMPL chains, ranked by confidence",
        "subgraph_vars": ["supporter"],
        # #6871: the operator's typed edges ARE its input set — idx 0 is the
        # operator's SOURCE, idx >= 1 its targets — so "the claim supporting a
        # target" is the idx=0 endpoint and the supported claim is an idx>=1
        # one. Without the two role guards a plain identity guard
        # (target.id <> supporter.id) also matches INVERSELY — the operator's
        # own source served as the SUPPORTER of a target: querying the source
        # returned the target as its supporter, and `tortoise_analyze` answered
        # with the claim's own hypothesis as its evidence.
        "cypher": """
            MATCH (supporter:Point)<-[r_in:IMPL]-(op:Point {op_type:\"IMPL\"})-[r_out:IMPL]->(target:Point)
            WHERE coalesce(r_in.idx, 0) = 0
              AND coalesce(r_out.idx, 0) >= 1
              AND target.id <> supporter.id
              AND target.content CONTAINS $entity
              AND coalesce(supporter.confidence, 0.5) > 0.0
            RETURN supporter.id, supporter.content, coalesce(supporter.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "supporting"),
    },
    "counter_arguments": {
        "triggers": ["counter", "against", "objection", "contradicts", "opposes", "attack"],
        "description": "Find claims that contradict a target via NAND edges",
        "subgraph_vars": ["opponent"],
        "cypher": """
            MATCH (target:Point)<-[:NAND]-(op:Point {op_type:\"NAND\"})-[:NAND]->(opponent:Point)
            WHERE target.id <> opponent.id
              AND target.content CONTAINS $entity
              AND opponent.is_operator = false
            RETURN opponent.id, opponent.content, coalesce(opponent.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "counter"),
    },
    "consensus": {
        "triggers": ["consensus", "agree", "shared", "common ground", "everyone believes"],
        "description": "Find high-confidence claims with no NAND attacks (consensus)",
        "subgraph_vars": ["c"],
        "cypher": """
            MATCH (c:Point)
            WHERE c.is_operator = false
              AND coalesce(c.confidence, 0.5) > 0.6
              AND NOT (c)<-[:NAND]-(:Point {op_type:\"NAND\"})
            RETURN c.id, c.content, coalesce(c.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "consensus"),
    },
    "most_uncertain": {
        "triggers": ["uncertain", "weakest", "least sure", "unknown", "unsure", "low confidence"],
        "description": "Find claims with lowest EP confidence or highest variance",
        "subgraph_vars": ["c"],
        # #2490: decayed terminals carry var 1/12 > the 0.04 contested bar and
        # would dominate "most uncertain" on include-terminal surfaces — the
        # shared live.py terminal predicate excludes them ("uncertain" is for
        # LIVE claims).
        # The WHERE is a FILTER — "this claim carries persisted EP state" —
        # not a measurement claim, so a #2199 declared baseline (prior-only)
        # is a legitimate candidate here: its variance is real and worth
        # ranking. The reported `conf`, however, must be the canonical belief
        # mean every other surface reads (#2206); `coalesce(c.confidence,0.5)`
        # reported 0.5 for a prior-only 0.75 claim — the residual #2206-class
        # divergence this fixes (#3415). The belief expression coerces with
        # toFloat: FalkorDB `/` is INTEGER division when both operands are ints
        # (unlike Python), so a prior persisted as ints (set_point_baseline(2,1),
        # or the legacy 49705bf6e tier_map) truncated 2/3 → 0 — worse than the
        # neutral fallback it replaced. Mirrors annotate_ep_batch's guard.
        "cypher": f"""
            MATCH (c:Point)
            WHERE c.is_operator = false
              AND {_terminal_excluded('c.status')}
              AND (c.posterior_alpha IS NOT NULL OR c.ep_alpha IS NOT NULL)
            WITH c, coalesce(c.posterior_alpha, c.ep_alpha, 1.0) AS a,
                 coalesce(c.posterior_beta, c.ep_beta, 1.0) AS b
            WITH c, a, b, (a * b) / 
               ((a + b)^2 * (a + b + 1)) as variance
            RETURN c.id, c.content,
                   coalesce(c.confidence,
                            CASE WHEN a + b > 0
                                 THEN toFloat(a) / (a + b) ELSE 0.5 END)
                     as conf,
                   variance
            ORDER BY variance DESC LIMIT $limit
        """,
        "format": lambda rows: _format_uncertain(rows),
    },
    "evidence_chain": {
        "triggers": ["evidence for", "supports", "basis", "foundation", "backing", "chain"],
        "description": "Trace IMPL support chain back to root evidence for a claim",
        "subgraph_vars": ["evidence"],
        # #6871: same role guard as `strongest_for` above — the evidence is the
        # operator's idx=0 endpoint, the chained claim an idx>=1 one. Without
        # it, asking for the evidence chain of the SOURCE returned its own
        # targets as its foundation.
        "cypher": """
            MATCH (evidence:Point)<-[r_in:IMPL]-(op:Point {op_type:\"IMPL\"})-[r_out:IMPL]->(target:Point)
            WHERE coalesce(r_in.idx, 0) = 0
              AND coalesce(r_out.idx, 0) >= 1
              AND target.id <> evidence.id
              AND target.content CONTAINS $entity
              AND evidence.is_operator = false
            RETURN evidence.id, evidence.content, coalesce(evidence.confidence,0.5) as conf
            ORDER BY conf DESC LIMIT $limit
        """,
        "format": lambda rows: _format_chain(rows),
    },
    "trends": {
        "triggers": ["changed", "trend", "evolved", "over time", "history", "how has"],
        "description": "Show confidence changes by comparing node properties over time",
        "subgraph_vars": ["c"],
        # #2490: exclude decayed terminals from the confidence timeline (see
        # most_uncertain — the shared live.py terminal predicate). #3415: the
        # reported conf is the canonical belief mean (posterior→prior
        # coalesce, #2206), never the neutral 0.5 — a prior-only 0.75 claim
        # used to print conf=0.50 beside its own α=3/β=1 columns. toFloat on
        # the numerator mirrors annotate_ep_batch: FalkorDB integer division
        # would truncate an int prior mean (2/3 → 0) — see most_uncertain.
        "cypher": f"""
            MATCH (c:Point)
            WHERE c.content CONTAINS $entity
              AND {_terminal_excluded('c.status')}
              AND (c.posterior_alpha IS NOT NULL OR c.ep_alpha IS NOT NULL)
            WITH c, coalesce(c.posterior_alpha, c.ep_alpha, 1.0) AS a,
                 coalesce(c.posterior_beta, c.ep_beta, 1.0) AS b
            RETURN c.id, c.content,
                   coalesce(c.confidence,
                            CASE WHEN a + b > 0
                                 THEN toFloat(a) / (a + b) ELSE 0.5 END)
                     as conf,
                   a, b, c.createdAt
            ORDER BY c.createdAt DESC LIMIT $limit
        """,
        "format": lambda rows: _format_timeline(rows),
    },
    "grounding": {
        "triggers": ["grounded", "central", "important", "key", "pivotal", "core"],
        "description": "Find most central claims by PageRank grounding score",
        "subgraph_vars": ["c"],
        "cypher": """
            MATCH (c:Point)
            WHERE c.is_operator = false
              AND c.grounding IS NOT NULL
            RETURN c.id, c.content, c.grounding
            ORDER BY c.grounding DESC LIMIT $limit
        """,
        "format": lambda rows: _format_ranked(rows, "grounding"),
    },
}


# ═══════════════════════════════════════════════════════════════════
# Formatters
# ═══════════════════════════════════════════════════════════════════

def _format_pairs(rows: list, label: str) -> str:
    if not rows:
        return f"No {label}s found."
    lines = [f"{len(rows)} {label}(s) found:"]
    for r in rows[:10]:
        lines.append(f"  • \"{r[1][:80]}\" ({r[2]:.2f}) ⇎ \"{r[4][:80]}\" ({r[5]:.2f})")
    return "\n".join(lines)


def _format_ranked(rows: list, label: str) -> str:
    if not rows:
        return f"No {label} claims found."
    lines = [f"Top {label} claims:"]
    for i, r in enumerate(rows[:10], 1):
        try:
            conf = float(r[2])
            content = r[1][:80] if r[1] is not None else ""
        except (TypeError, ValueError, IndexError):
            conf = None  # non-numeric/missing confidence → degrade, don't crash
            content = (r[1][:80] if len(r) > 1 and r[1] is not None else "")
        val = f"  {i}. \"{content}\"" + (f" (confidence: {conf:.2f})" if conf is not None else "")
        lines.append(val)
    return "\n".join(lines)


def _format_uncertain(rows: list) -> str:
    if not rows:
        return "No uncertain claims found."
    lines = ["Most uncertain claims (highest variance):"]
    for i, r in enumerate(rows[:10], 1):
        lines.append(f"  {i}. \"{r[1][:80]}\" (conf: {r[2]:.2f}, var: {r[3]:.4f})")
    return "\n".join(lines)


def _format_chain(rows: list) -> str:
    if not rows:
        return "No evidence chain found."
    lines = ["Evidence chain (ordered by confidence):"]
    for r in rows[:10]:
        # evidence_chain template returns 3 columns: id, content, conf
        try:
            conf = float(r[2])
            conf_txt = f" (conf: {conf:.2f})"
        except (TypeError, ValueError, IndexError):
            conf_txt = ""  # non-numeric/missing confidence → degrade, don't crash
        content = r[1][:80] if len(r) > 1 and r[1] is not None else ""
        lines.append(f"  \"{content}\"{conf_txt}")
    return "\n".join(lines)


def _format_timeline(rows: list) -> str:
    if not rows:
        return "No timeline data found."
    lines = ["Confidence timeline:"]
    for r in rows[:10]:
        ts = r[5][:10] if len(r) > 5 and r[5] else "?"
        lines.append(f"  [{ts}] \"{r[1][:80]}\" (α={r[3]:.1f}, β={r[4]:.1f}, conf={r[2]:.2f})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Classification
# ═══════════════════════════════════════════════════════════════════

def classify(question: str) -> tuple[str, dict] | None:
    """Keyword match question to template pattern. Returns (name, params) or None."""
    q = question.lower()
    for name, tmpl in TEMPLATES.items():
        for trigger in tmpl["triggers"]:
            if trigger in q:
                # Extract entity parameter
                entity = _extract_entity(q, trigger)
                return name, {"entity": entity, "limit": 20}
    return None


def _extract_entity(question: str, trigger: str) -> str:
    """Extract entity name from question by removing trigger words and common phrases."""
    # Simple heuristic: remove the trigger and common surrounding words
    q = question.lower()
    # Remove common phrases
    for phrase in ["what is the", "what are the", "show me", "find", "tell me about",
                    "what claims", "which claims", "where is the", "how has"]:
        q = q.replace(phrase, "")
    # Remove the trigger word
    q = q.replace(trigger, "")
    # Remove common connector words
    for word in ["about", "for", "of", "the", "in", "on", "to", "that", "is", "are"]:
        q = re.sub(rf'\b{word}\b', '', q)
    # Clean up
    entity = ' '.join(q.split()).strip()
    return entity if entity else ""


# ═══════════════════════════════════════════════════════════════════
# LLM Integration (optional — keyword match handles 80% of queries)
# ═══════════════════════════════════════════════════════════════════

# #329: provider-key pairing — a key is ONLY ever sent to the provider that
# issued it. (The old code used `OPENAI_API_KEY or DEEPSEEK_API_KEY` and always
# POSTed to api.deepseek.com — the OpenAI key was exfiltrated to DeepSeek.)
#
# #4129: `deepseek-v4-flash` is RETIRED — api.deepseek.com answers 200 to it and
# silently serves `deepseek-flash`, so the id named was not the model that ran.
_DEEPSEEK_FLASH_IDS: tuple[str, ...] = ("deepseek-flash", "deepseek-v4-flash")


def _is_deepseek_flash_family(model_id: str) -> bool:
    """True for a DeepSeek flash-family id, dated builds included.

    Matched on the LAST path segment, so an OpenRouter route
    (``deepseek/deepseek-v4-flash``) is recognised, and deliberately NOT on a
    single literal: keying the #1790 thinking-disable on one exact id is what
    let a rename (``deepseek-v4-flash`` -> ``deepseek-flash``) silently
    re-enable thinking, with no failing test to say so.
    """
    tail = model_id.rsplit("/", 1)[-1]
    return any(tail == i or tail.startswith(i + "-") for i in _DEEPSEEK_FLASH_IDS)


_LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "DEEPSEEK_API_KEY": ("https://api.deepseek.com/v1/chat/completions", "deepseek-flash"),
    "OPENAI_API_KEY": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
}
# Priority order when multiple keys are set (deepseek first — historical default).
_LLM_PROVIDER_PRIORITY: tuple[str, ...] = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")


def llm_classify(question: str) -> tuple[str, dict] | None:
    """Use LLM to classify intent + extract params when keyword match fails.

    #329: picks the provider whose OWN env key is set; an empty-string key is
    treated as unset. The chosen key is only sent to its own provider's URL.
    Returns None (keyword-only fallback) when no key is set or the provider
    request fails — a provider outage never falls through to another provider
    (that would leak the wrong key too).
    """
    import urllib.request
    api_key = None
    provider_url = None
    provider_model = None
    for env_name in _LLM_PROVIDER_PRIORITY:
        candidate = os.environ.get(env_name)
        if candidate:  # non-empty string
            api_key, (provider_url, provider_model) = candidate, _LLM_PROVIDERS[env_name]
            break
    if api_key is None:
        return None  # fall back to keyword only

    try:
        body = {
            "model": provider_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": LLM_PROMPT},
                         {"role": "user", "content": question}],
        }
        # #1790: the deepseek flash family reasons by DEFAULT (thinking: high)
        # and collapses into hidden reasoning tokens — disable thinking for the
        # flash family ONLY (OpenAI would 400 on the unknown param). Matched on
        # the FLASH FAMILY rather than one literal id: a single-literal gate goes
        # silently inert the moment the id is renamed (#4129), which re-enables
        # thinking with no failing test and no log line. A future pro entry in
        # _LLM_PROVIDERS must still NOT disable thinking.
        if _is_deepseek_flash_family(provider_model):
            body["thinking"] = {"type": "disabled"}
        body = json.dumps(body).encode()
        req = urllib.request.Request(
            provider_url,
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            result = json.loads(data["choices"][0]["message"]["content"])
            return result.get("pattern"), result.get("params", {})
    except Exception:
        return None


LLM_PROMPT = """You classify questions about an epistemic graph. Output JSON:
{"pattern": "<name>", "params": {"entity": "...", "limit": 20}}

Available patterns:
- disagreement: "where is the disagreement/conflict?"
- strongest_for: "what supports/strongest evidence for X?"
- counter_arguments: "what contradicts/attacks X?"
- consensus: "what do we agree on?"
- most_uncertain: "what are we least sure about?"
- evidence_chain: "trace the evidence for X"
- trends: "how has X changed over time?"
- grounding: "what are the most central/important claims?"

Only output JSON. No other text."""


# ═══════════════════════════════════════════════════════════════════
# Subgraph Filter Injection
# ═══════════════════════════════════════════════════════════════════

def _inject_subgraph_filter(cypher: str, vars: list[str],
                            ids: set[str]) -> str:
    """Inject ID filter into Cypher WHERE clause for entity-scoped analysis."""
    if not vars:
        return cypher
    id_list = json.dumps(list(ids))
    filters = ' AND '.join(f'{v}.id IN {id_list}' for v in vars)
    return cypher.replace('WHERE ', f'WHERE {filters} AND ', 1)


# ═══════════════════════════════════════════════════════════════════
# Main API
# ═══════════════════════════════════════════════════════════════════

# A9 (epic #902, issue #1059): the selector traverses operator-less direct
# edges — probed by SENTINEL_A9 (plan §5.6/§7 capability-probe sentinels).
DIRECT_EDGE_TRAVERSAL = True


def _bfs_select_operators(proj, anchors: list[str], max_hops: int | None = 1,
                          rel_filter: str = "IMPL|NAND",
                          direction: str = "both",
                          include_draft: bool = False,
                          op_cap: int = 200,
                          stats: dict[str, Any] | None = None
                          ) -> tuple[set[str], set[tuple[str, str, str]]]:
    """BFS subgraph selection from anchor epistemic nodes — A9 return contract.

    Returns ``(operator_ids, factor_anchors)``:
      operator_ids  — operator Point IDs for the operator-mediated factors
      factor_anchors — DIRECT-EDGE factor anchors ``(src, tgt, type)`` — one
        per operator-less IMPL/NAND edge discovered in the traversal,
        DEDUPED by (src, tgt, type) regardless of BFS discovery direction
        (a bidirectional walk can discover the same edge from both
        endpoints; a double anchor would run the factor twice → wrong
        posteriors with green tests, §5.6 cycle-3).

    A9 traversal (epic #902 §5.6): the selector ALSO walks operator-less
    direct edges — DIRECTION-RESPECTING (#7852): a direct edge is walked
    forward only where its stored ``direction`` flows forward
    (``->``/``<->`` and their legacy aliases) and backward only where it
    flows backward (``<-``/``<->`` and their aliases); ``-`` (no confidence
    transfers) is walked NEITHER way. The one historical exception is NAND:
    it keeps its always-both legacy behaviour (never back-propagating into a
    forward-only IMPL source is an IMPL rule), minus the new ``-``. The
    direct-edge traversal expands
    the frontier like the operator BFS does, so mixed subgraphs select
    operators AND direct factors together; a direct-edge-only subgraph
    yields ZERO operator ids + non-empty factor anchors (the return
    contract).

    max_hops (#395 delta A): int k = k BFS hops; None = unbounded — expand
    until the frontier is empty (the full connected subgraph from the
    anchors, direction/rel-filter constrained). For explicit k the operator
    cap (op_cap, default 200) stays a mid-BFS safety bound; for None the cap
    is lifted (genuine full closure) — internal callers only (the
    user-facing tortoise_analyze HTTP tool keeps an explicit k).

    op_cap (#1241, epic 903-C3): truncation bound for explicit-k runs. The
    stale-first scheduler passes its per-pass operator budget through so an
    explicit budget OVERRIDES the default 200-op cap (budget > 200 lifts it;
    budget=None keeps the existing cap). Deterministic truncation — sorted
    by id — in all cases.

    DERIVED-LIVENESS (GATE-2 Q3, §5.6 item 5): an operator participates in
    EP IFF >=2 of its connected points are live (status='live') — the
    fail-closed gated-rejection (old check-5) is RETIRED; gated+
    operator-requiring bundles are ACCEPTED and the operator is EP-INERT
    until its endpoints are live. The predicate counts the operator's
    IMPL|NAND-connected points (source AND targets, undirected).

    With include_draft=False (default, #780): draft anchors, draft operators
    and draft frontier points are excluded — the shared live-only filter at
    the analyze-path call site of the four-factor-extraction set.


    ``stats`` (#6211): OPTIONAL out-parameter. When a dict is passed it is
    FILLED with what the selection actually did:

      ``truncated``      — True iff ``op_cap`` bit (the operator set this
                           returns is a strict subset of what the BFS found)
      ``operators_seen`` — operators the traversal COLLECTED before any cap
      ``operators_used`` — operators this selection RETURNS
      ``op_cap``         — the bound in force (None when lifted)

    The return value stays a 2-tuple: every existing caller is unchanged, and
    the signal is opt-in because most existing callers already own their bound
    (the Dreamer passes ``op_cap=budget`` and publishes ``budget_used``). It
    exists for the callers that do NOT own the cap — ``sdk.compute_confidence``
    — which otherwise cannot tell a full-graph ranking from a capped one: the
    stderr line is invisible to a programmatic caller, and a capped EP ranking
    is a confident number computed over a truncated topology (#6211).

    The dict is OVERWRITTEN for THIS call on every return path, never merged:
    a caller may safely reuse one dict across calls (a stale ``truncated`` must
    not leak into a later uncapped selection — #6211 review round 1).

    EPISTEMIC LABELS (#7927): an anchor, an operator's input and a direct-edge
    endpoint may each be a :Subject/:Object/:Event (§7813), so every leg below
    admits ``live.EPISTEMIC_LABELS`` — ``epistemic_label_queries`` on an
    ID-ANCHORED position (label kept, so the per-(label, property) index
    still anchors the read) and ``epistemic_disjunction`` on a TRAVERSAL
    position. Before this, a non-Point anchor selected nothing and an operator
    whose only second endpoint was non-Point looked EP-inert.
    """
    import logging
    _log = logging.getLogger(__name__)

    def _sweep(template: str, params: dict, labels: list[str]) -> list:
        """Sweep this hop's anchored legs over the labels PRESENT (#7927).

        Thin binding of ``live.epistemic_sweep`` to this projection — the
        frontier/anchor can hold a :Subject/:Object/:Event (§7813 ruling), so
        an anchored leg keeps its LABEL (the per-(label, property) index) and
        expands through ``live.epistemic_label_queries``; ``labels`` is the
        RESOLVED set for this hop, so a Point-only graph pays one query per leg
        instead of four (an unconditional sweep took the #395 AC5 40-claim
        zone from ~0.73s to ~1.50s against a 1.2s bound).
        """
        return epistemic_sweep(proj.g, template, params, labels=labels)

    collected: set[str] = set()
    factor_anchors: set[tuple[str, str, str]] = set()
    # #6211 review round 1: truncation is tracked LOCALLY, never read back out
    # of the caller-owned `stats` dict — a reused dict carrying an earlier
    # call's `truncated: True` would otherwise skip the final write below and
    # report the previous call's numbers as this one's.
    truncated = False
    frontier: set[str] = set(anchors)
    visited: set[str] = set(anchors)
    rel_types = set(rel_filter.split("|"))

    live_op = f"AND {_live_only('op.status', include_draft)}"
    live_t = f"AND {_live_only('target.status', include_draft)}"
    live_p = f"AND {_live_only('p.status', include_draft)}"
    live_b = f"AND {_live_only('b.status', include_draft)}"
    live_a = f"AND {_live_only('a.status', include_draft)}"

    # derived-liveness (GATE-2 Q3): operator participates IFF >=2 of its
    # connected points (IMPL|NAND neighbors, both directions) are live.
    # Unconditional — "live" is an absolute status (the escape hatch
    # include_draft=True widens the operator's own status filter, not the
    # endpoint-liveness rule).

    def _derived_live_operators(ids: list[str]) -> set[str]:
        """Subset of ``ids`` whose operators have >=2 live connected points.

        GATE-2 Q3 derived-liveness: an operator participates in EP iff >=2 of
        its connected points (IMPL|NAND neighbors, both directions) are live.
        "Live" matches the shared #780/#2422 ``_live_only`` semantics
        (live.py): ``status IS NULL OR status <> 'draft'`` AND NOT terminal
        (retracted/superseded/outdated/archived/deprecated status — the
        canonical ``live.TERMINAL_EXCLUDED_STATUSES`` — or the
        ``outdated=true`` flag) — legacy pre-#780 nodes without a stored
        status are LIVE (the entity write path defaults
        ``coalesce($st, n.status, 'live')``). The predicate is UNCONDITIONAL
        (draft AND terminal endpoints never count toward the >=2, even under
        the include_draft escape hatch — the E2E-13.1 1-live/1-draft
        boundary pins the operator INERT; #2422 extends the same exclusion
        to terminal endpoints).
        """
        if not ids:
            return set()
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[:IMPL|NAND]-(t) "
            "WHERE op.id IN $ids "
            # #7927: ``t`` is a TRAVERSAL position reached from the anchored
            # operator — the claim a non-Point belief feeds is epistemic too.
            f"AND {epistemic_disjunction('t')} "
            # #3139/#3154: index-independent form — a bare `= false` is
            # emptied by a GRAPH.COPY'd boolean index, making every operator
            # look inert (zero live connections) and silently starving the
            # dream selector of factors.
            "AND (t.is_operator IS NULL OR t.is_operator = false) "
            "AND t.op_type IS NULL "
            f"AND {_live_only('t.status')} "
            "WITH op, count(DISTINCT t) AS live_conn "
            "WHERE live_conn >= 2 "
            "RETURN op.id",
            params={"ids": ids},
        ).result_set
        return {r[0] for r in rows}

    if not include_draft and frontier:
        # #7927: id-anchored draft-anchor probe — sweep the labels PRESENT in
        # the anchor set, or a draft :Subject/:Object/:Event anchor is invisible
        # here and the live-only filter silently keeps it.
        rows = _sweep(
            "MATCH (n:{label}) WHERE n.id IN $ids AND n.status = 'draft' "
            "RETURN n.id",
            {"ids": list(frontier)},
            epistemic_labels_of(proj.g, list(frontier)),
        )
        draft_anchors = {r[0] for r in rows}
        if draft_anchors:
            frontier -= draft_anchors
            visited -= draft_anchors

    hop = 0
    while frontier and (max_hops is None or hop < max_hops):
        if not frontier:
            break
        new_ops: set[str] = set()
        new_points: set[str] = set()
        frontier_list = list(frontier)
        # #7927: resolve this hop's labels ONCE (one query on a Point-only
        # frontier) and reuse them for every id-anchored leg below.
        frontier_labels = epistemic_labels_of(proj.g, frontier_list)

        for rel in rel_types:
            if rel not in ("IMPL", "NAND"):
                continue
            safe_rel = rel.replace("'", "")

            # NAND is always bidirectional; IMPL respects direction flag
            if rel == "NAND":  # noqa: SIM114
                dirs = ("incoming", "outgoing")
            elif direction == "both":
                dirs = ("incoming", "outgoing")
            else:
                dirs = (direction,)

            # ── operator-mediated edges (existing BFS + derived-liveness) ──
            for d in dirs:
                if d == "incoming":
                    # frontier POINTS → operators pointing AT them; only
                    # operators with >=2 live connected points participate
                    # #7927: ``p`` is ID-ANCHORED (`p.id IN $frontier`) —
                    # label-swept so a :Subject/:Object/:Event anchor finds
                    # the operators pointing at it.
                    rows = _sweep(
                        ("MATCH (op:Point {is_operator:true})-[:{rel}]->(p:{label}) "
                         f"WHERE p.id IN $frontier {live_op} "
                         "RETURN DISTINCT op.id").replace("{rel}", rel),
                        {"frontier": frontier_list},
                        frontier_labels,
                    )
                    for op_id in _derived_live_operators([r[0] for r in rows]):
                        new_ops.add(op_id)
                        new_points.add(op_id)
                else:  # outgoing
                    # frontier OPERATORS → their target points (expansion);
                    # only derived-live operators expand (an inert operator
                    # must not reach its targets)
                    alive = _derived_live_operators(frontier_list)
                    if alive:
                        # #7927: ``target`` is a TRAVERSAL position reached
                        # from the anchored operator.
                        rows = proj.g.query(
                            "MATCH (op:Point {is_operator:true})-[:" + safe_rel + "]->(target) "
                            f"WHERE op.id IN $ops {live_op} {live_t} "
                            f"AND {epistemic_disjunction('target')} "
                            # A keyless legacy ``:Event`` (``session_indexer`` mints
                            # ``(e:Event {eventId:…})`` with no ``id``) has NO
                            # addressable identity for an id-keyed frontier, so its
                            # widened return would be a ``None`` id in ``new_points``
                            # and in ``compute_confidence``'s seeds. Requiring ``.id``
                            # keeps the population at exactly what the PRE-widening
                            # label predicate admitted — ``coalesce(id, eventId)`` here
                            # would hand back a value that is not an ``id``
                            # (``navigation._node_identity``, #8538; #7927 review).
                            "AND target.id IS NOT NULL "
                            "RETURN DISTINCT target.id",
                            params={"ops": list(alive)},
                        ).result_set
                        for (target_id,) in rows:
                            new_points.add(target_id)

            # ── operator-LESS direct edges (A9, §5.6) — DIRECTION-RESPECTING ──
            # The `direction` parameter governs the direct-edge traversal too:
            # forward (frontier → neighbor) only for "outgoing"/"both";
            # backward (neighbor → frontier) only for "incoming"/"both" —
            # mirroring the operator-mediated dirs. NAND is ALWAYS both
            # (matching the operator BFS's NAND bidirectionality).
            # forward: (frontier) -[rel]-> (plain neighbor); every rel type
            # traverses forward from the frontier point. #7852: only where the
            # edge's direction actually flows forward (`->`/`<->` and their
            # legacy aliases) — a backward-only `<-` edge is never walked the
            # wrong way.
            if rel == "NAND" or direction in ("outgoing", "both"):
                # #7927: ``a`` is ID-ANCHORED (`a.id IN $frontier`) → label
                # sweep; ``b`` is the TRAVERSAL position → WHERE disjunction.
                fwd_tpl = (
                    "MATCH (a:{label})-[r:" + safe_rel + "]->(b) "
                    f"WHERE a.id IN $frontier {live_a} {live_b} "
                    f"AND {epistemic_disjunction('b')} "
                    # A keyless legacy ``:Event`` (``session_indexer`` mints
                    # ``(e:Event {eventId:…})`` with no ``id``) has NO
                    # addressable identity for an id-keyed frontier, so its
                    # widened return would be a ``None`` id in ``new_points``
                    # and in ``compute_confidence``'s seeds. Requiring ``.id``
                    # keeps the population at exactly what the PRE-widening
                    # label predicate admitted — ``coalesce(id, eventId)`` here
                    # would hand back a value that is not an ``id``
                    # (``navigation._node_identity``, #8538; #7927 review).
                    "AND b.id IS NOT NULL "
                    # #3139/#3154: index-independent non-operator predicate.
                    "AND (a.is_operator IS NULL OR a.is_operator = false) "
                    "AND a.op_type IS NULL "
                    "AND (b.is_operator IS NULL OR b.is_operator = false) "
                    "AND b.op_type IS NULL "
                    "AND (type(r) = 'NAND' "
                    "     AND coalesce(r.direction, 'bidirectional') <> '-' "
                    "     OR coalesce(r.direction, 'bidirectional') IN "
                    "        ['bidirectional', '<->', '->', 'unidirectional']) "
                    "RETURN DISTINCT b.id, a.id, type(r)"
                )
                rows = _sweep(fwd_tpl, {"frontier": frontier_list}, frontier_labels)
                for (bid, aid, rtype) in rows:
                    if bid not in visited:
                        new_points.add(bid)
                    factor_anchors.add((aid, bid, rtype))
            # backward: (plain neighbor) -[rel]-> (frontier); NAND always;
            # IMPL only where the edge flows BACKWARD (`<-`/`<->` and their
            # legacy aliases — never back-propagate into a forward-only edge).
            if rel == "NAND" or direction in ("incoming", "both"):
                # #7927: ``b`` is ID-ANCHORED (`b.id IN $frontier`) → label
                # sweep; ``a`` is the TRAVERSAL position → WHERE disjunction.
                bwd_tpl = (
                    "MATCH (a)-[r:" + safe_rel + "]->(b:{label}) "
                    f"WHERE b.id IN $frontier {live_a} {live_b} "
                    f"AND {epistemic_disjunction('a')} "
                    # A keyless legacy ``:Event`` (``session_indexer`` mints
                    # ``(e:Event {eventId:…})`` with no ``id``) has NO
                    # addressable identity for an id-keyed frontier, so its
                    # widened return would be a ``None`` id in ``new_points``
                    # and in ``compute_confidence``'s seeds. Requiring ``.id``
                    # keeps the population at exactly what the PRE-widening
                    # label predicate admitted — ``coalesce(id, eventId)`` here
                    # would hand back a value that is not an ``id``
                    # (``navigation._node_identity``, #8538; #7927 review).
                    "AND a.id IS NOT NULL "
                    # #3139/#3154: index-independent non-operator predicate.
                    "AND (a.is_operator IS NULL OR a.is_operator = false) "
                    "AND a.op_type IS NULL "
                    "AND (b.is_operator IS NULL OR b.is_operator = false) "
                    "AND b.op_type IS NULL "
                    "AND (type(r) = 'NAND' "
                    "     AND coalesce(r.direction, 'bidirectional') <> '-' "
                    "     OR coalesce(r.direction, 'bidirectional') IN "
                    "        ['bidirectional', '<->', '<-']) "
                    "RETURN DISTINCT a.id, b.id, type(r)"
                )
                rows = _sweep(bwd_tpl, {"frontier": frontier_list}, frontier_labels)
                for (aid, bid, rtype) in rows:
                    if aid not in visited:
                        new_points.add(aid)
                    # the anchor is the edge's CANONICAL (src, tgt) — the same
                    # (a.id, b.id, type) tuple the forward query records, so a
                    # bidirectional walk dedups to ONE anchor per edge (§5.6)
                    factor_anchors.add((aid, bid, rtype))

        collected |= new_ops

        # #395 (delta A): the op_cap is a safety bound for EXPLICIT k only —
        # max_hops=None means genuine full connected subgraph, so the cap is
        # lifted in the unbounded regime (degeneration guarding for the
        # interactive path lives in ep._affected_claims; this selector feeds
        # dream/anchors, both explicit-k by default). #1241: the scheduler
        # passes its per-pass operator budget as op_cap (explicit budget
        # overrides the 200 default; budget=None keeps the cap).
        if (max_hops is not None and op_cap is not None
                and len(collected) > op_cap):
            _log.warning(
                "BFS selector: collected %d operators, truncating to %d.",
                len(collected), op_cap,
            )
            if stats is not None:
                # #6211: the machine-readable half of the warning above. The
                # log line stays (an operator reading stderr still sees it);
                # the dict is what a programmatic caller can act on.
                truncated = True
                stats["truncated"] = True
                stats["operators_seen"] = len(collected)
                stats["operators_used"] = op_cap
                stats["op_cap"] = op_cap
            # Deterministic truncation — set iteration order is non-deterministic
            # (hash randomization); sort by ID for reproducible selection.
            collected = set(sorted(collected)[:op_cap])
            break

        # Expand to new frontier points from collected operators
        if new_ops and (max_hops is None or hop < max_hops - 1):
            ops_list = list(new_ops)
            rows = proj.g.query(
                "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(p) "
                f"WHERE op.id IN $ops {live_p} "
                # #7927: ``p`` is reached from the anchored operator.
                f"AND {epistemic_disjunction('p')} "
                # The EXPANSION frontier is re-anchored by ``p.id`` on the next
                # hop, so a node with no ``id`` cannot continue it: requiring
                # it keeps the identity-less legacy ``:Event`` population at
                # exactly what the pre-widening label predicate admitted
                # (``coalesce`` would re-anchor on a value that is not an ``id``
                # — #8538; #7927 review).
                "AND p.id IS NOT NULL "
                "RETURN DISTINCT p.id",
                params={"ops": ops_list},
            ).result_set
            for (p_id,) in rows:
                if p_id not in visited:
                    new_points.add(p_id)

        frontier = new_points - visited
        visited |= new_points
        hop += 1

    if stats is not None and not truncated:
        # Un-capped (or cap never reached): seen == used. Written on the normal
        # return too, so a caller always gets both numbers instead of having to
        # treat an absent key as "no cap" (#6211). OVERWRITES any values a
        # reused dict carried in, so the report always describes THIS call.
        stats["truncated"] = False
        stats["operators_seen"] = len(collected)
        stats["operators_used"] = len(collected)
        stats["op_cap"] = op_cap if max_hops is not None else None

    return collected, factor_anchors


# ═══════════════════════════════════════════════════════════════════
# Epic 903 (dreaming): staleness-ranked claim selection (shared helper)
# ═══════════════════════════════════════════════════════════════════


def _stale_first_claims(proj, limit: int | None = None) -> list[str]:
    """Staleness-ranked live non-operator claim ids (epic 903-C3, #1241).

    Ranks non-operator, non-draft Points by ``lastDreamedAt ASC`` with
    NULL = STALEST (FIRST) — legacy / first-deploy / crash-mid-pass claims
    (never dreamed) drain across scheduler passes. Deterministic tie-break
    by id. The scheduler's window = top-N of this ranking ∪ retained dirty
    roots; a future staleness report (I2, deferred at human gate 2) is a
    pure function of this same ranked list.

    Null semantics (D2-3): the ORDER BY keys on ``coalesce(n.lastDreamedAt,
    '')`` so nulls rank first REGARDLESS of the backend's native null
    ordering (FalkorDB sorts NULL last in ASC — a raw ``ORDER BY
    n.lastDreamedAt`` would rank never-dreamed claims FRESHEST, the
    opposite of the contract). This is the plan's explicit-null-scan-union
    alternative: one deterministic query instead of a union scan, at the
    cost of not sorting on the raw indexed property (the plain :Point(
    lastDreamedAt) index still accelerates the property access; #3154
    retired the :Point(is_operator, lastDreamedAt) composite — no engine
    indexes the boolean property).
    """
    base = (
        "MATCH (n:Point) "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        f"AND {_live_only('n.status')} "
        "WITH n, coalesce(n.lastDreamedAt, '') AS _freshness "
        "ORDER BY _freshness ASC, n.id ASC "
        "RETURN n.id"
    )
    rows = proj.g.query(
        base if limit is None else base + " LIMIT $limit",
        params={} if limit is None else {"limit": limit},
    ).result_set
    return [r[0] for r in rows]


def _stale_first_count_stamped(proj) -> int:
    """Count live non-operator claims that HAVE a ``lastDreamedAt`` stamp.

    The scheduler's all-null decision (plan W2): a graph where NOTHING has
    ever been dreamed (first deploy / legacy / crash mid-pass) degenerates
    to a single full pass — window = whole graph — regardless of budget.
    """
    rows = proj.g.query(
        "MATCH (n:Point) "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        f"AND {_live_only('n.status')} "
        "AND n.lastDreamedAt IS NOT NULL "
        "RETURN count(n)"
    ).result_set
    return int(rows[0][0])


def analyze(question: str, proj=None, *,
            entity_subgraph_ids: set[str] | None = None,
            anchor_ids: list[str] | None = None,
            max_hops: int = 1,
            rel_filter: str = "IMPL|NAND",
            direction: str = "both",
            use_llm: bool = True) -> dict[str, Any]:
    """Answer a natural language question about the Tortoise graph.

    Args:
        question: Natural language question
        proj: FalkorProjection instance (optional — for Cypher execution)
        entity_subgraph_ids: Pre-filter results to these Point IDs (entity-scoped analysis)
        anchor_ids: list of Point IDs for BFS subgraph selection (new; alternative to entity_subgraph_ids)
        anchor_ids: list of Point IDs for BFS subgraph selection (new; alternative to entity_subgraph_ids)
        max_hops: BFS expansion depth when using anchor_ids (default 1)
        rel_filter: edge types for BFS — "IMPL", "NAND", or "IMPL|NAND" (default)
        direction: IMPL edge traversal direction — "incoming", "outgoing", or "both" (default)
        use_llm: Fall back to LLM if keyword match fails

    Returns:
        {"answer": "...", "raw": [...], "pattern": "...", "query": "..."}
    """
    # BFS subgraph selection from anchor IDs
    if anchor_ids is not None and proj is not None and entity_subgraph_ids is None:
        _ops, _anchors = _bfs_select_operators(
            proj, anchor_ids, max_hops=max_hops,
            rel_filter=rel_filter, direction=direction,
        )
        # A9 (epic #902): the subgraph carries the direct-edge factor-anchor
        # endpoints too (a direct-edge-only subgraph yields zero operators
        # but a non-empty direct-factor selection, §5.6 return contract).
        entity_subgraph_ids = _ops | {p for (s, t, _t) in _anchors for p in (s, t)}
    # 1. Classify intent
    result = classify(question)
    if result is None and use_llm:
        result = llm_classify(question)
    if result is None:
        return {"answer": "I couldn't understand that question. Try asking about: "
                          "disagreement, strongest claims, counter-arguments, consensus, "
                          "uncertainty, evidence chains, trends, or grounding.",
                "raw": [], "pattern": None, "query": None}

    pattern_name, params = result
    tmpl = TEMPLATES.get(pattern_name)
    if tmpl is None:
        # #329: pattern_name comes from the LLM (untrusted) — never echo it raw
        return {"answer": "Unknown pattern requested.", "raw": [], "pattern": None, "query": None}

    # 2. Execute Cypher
    cypher = tmpl["cypher"]
    if entity_subgraph_ids is not None:
        cypher = _inject_subgraph_filter(cypher, tmpl.get("subgraph_vars", []),
                                          entity_subgraph_ids)

    rows = []
    if proj is not None:
        try:
            rows = proj.g.query(cypher, params=params).result_set
        except Exception as e:
            # #329: redact — tenants must not see raw DB errors or query text
            from .security import redact_error
            return {"answer": f"Query error: {redact_error(e)}", "raw": [], "pattern": pattern_name, "query": None}

    # 3. Format (wrapped — a malformed value must degrade, never crash the
    # whole analyze surface; security: redacted, no raw internals)
    formatter = tmpl.get("format")
    try:
        if formatter:  # noqa: SIM108
            answer = formatter(rows)
        else:
            answer = f"Found {len(rows)} results."
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(
            "analyze formatter failed for pattern %s", pattern_name)
        from .security import redact_error
        answer = f"Formatting error: {redact_error(e)}"

    return {
        "answer": answer,
        "raw": [[str(v) for v in row] for row in rows[:20]],
        "pattern": pattern_name,
        "query": cypher,
    }


# ═══════════════════════════════════════════════════════════════════
# Gate B Tooling (epic-264 #779) — mean grounding + drift snapshot
# ═══════════════════════════════════════════════════════════════════

# Mean-absolute drift ceiling. MUST stay 0.02 — EpSafeCommit's
# max_grounding_drift (#785 seam) consumes this contract; tests pin the value.
MAX_GROUNDING_DRIFT = 0.02
# Max single-point absolute delta ceiling (R12: mean-absolute can mask
# per-point flips — the ≤5% max single-point target from the #779 issue).
MAX_POINT_DRIFT = 0.05


def _resolve_proj(proj):
    """Resolve a projection when None (lazy import — sdk/analyze are
    mutually imported at call sites, never at module load)."""
    if proj is not None:
        return proj
    from tortoise.sdk import TortoiseSDK
    return TortoiseSDK()._get_proj()


def mean_grounding(proj=None) -> float:
    """Mean over ``confidence`` of live non-operator Points (DE2E-4, #779).

    Gate B snapshot metric: sample = full live Point set, mean of the
    ``confidence`` property. Live semantics follow the #780 shared
    ``_live_only`` filter — legacy NULL-status Points are LIVE
    (``coalesce($st, n.status, 'live')`` write default), ``status: draft``
    Points are excluded. Operator Points (``is_operator: true``) are
    excluded. An empty live set returns 0.0.

    Missing-confidence extension (DELIBERATE, beyond the pinned DE2E-4
    formula): a live Point with no ``confidence`` contributes 0.5 to the
    mean (``coalesce(p.confidence, 0.5)`` — repo-wide NULL-confidence
    convention). The DE2E-4 plan formula assumes every Point carries a
    confidence; this implementation deliberately extends it so a
    confidence-less Point degrades the mean toward neutral 0.5 instead of
    erroring or being silently dropped (which would skew the mean upward).
    Asserted in ``test_mean_grounding_null_confidence_imputed_zero_five``.

    Consumed by #785's EpSafeCommit seam (resolves
    ``tortoise.analyze.mean_grounding``) for the pre/post batch check.
    """
    proj = _resolve_proj(proj)
    rows = proj.g.query(
        "MATCH (p:Point) "
        f"WHERE p.is_operator = false AND {_live_only('p.status')} "
        "RETURN coalesce(p.confidence, 0.5)"
    ).result_set
    if not rows:
        return 0.0
    return sum(float(r[0]) for r in rows) / len(rows)


def grounding_snapshot(proj=None) -> dict:
    """Pre/post batch grounding sample (Gate B tooling).

    Returns per-point confidences so BOTH ceilings can be checked: the
    ≤2% mean-absolute ceiling (``MAX_GROUNDING_DRIFT``) and the ≤5% max
    single-point absolute delta (``MAX_POINT_DRIFT``, R12).

    Returns:
        {"count": int, "mean": float, "points": {id: confidence},
         "sampled_at": ISO-8601 UTC}
    """
    proj = _resolve_proj(proj)
    rows = proj.g.query(
        "MATCH (p:Point) "
        f"WHERE p.is_operator = false AND {_live_only('p.status')} "
        "RETURN p.id, coalesce(p.confidence, 0.5)"
    ).result_set
    points = {r[0]: float(r[1]) for r in rows}
    mean = sum(points.values()) / len(points) if points else 0.0
    from datetime import datetime, timezone
    return {
        "count": len(points),
        "mean": mean,
        "points": points,
        "sampled_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }


def grounding_drift(pre: dict, post: dict, *,
                    max_mean_drift: float = MAX_GROUNDING_DRIFT,
                    max_point_drift: float = MAX_POINT_DRIFT) -> dict:
    """Compare pre/post batch grounding snapshots (Gate B drift check).

    Ceilings: ≤2% mean absolute (matches EpSafeCommit's
    ``max_grounding_drift=0.02`` — the #785 seam) and ≤5% max single-point
    absolute delta (R12 — a mean-absolute check alone can mask per-point
    flips). Both the per-point deltas AND the mean term are computed over
    the ID intersection (``common``) only: a Point created or deleted
    between snapshots is new/removed content, not a regression, and must
    not fail the check — a set-size change must not shift the mean past
    the ≤2% ceiling either. ``mean_abs_delta`` is the intersection-scoped
    delta (ignores pre/post ``mean`` fields; recomputed from ``points``).

    Returns:
        {"passed": bool, "mean_abs_delta": float,
         "max_point_abs_delta": float, "mean_ceiling": float,
         "point_ceiling": float, "pre_count": int, "post_count": int,
         "overlap": int} — plus "reason": "no_common_points" when the ID
        intersection is empty (review round 2: a total replacement must
        FAIL CLOSED, never vacuously pass).
    """
    pre_points = pre.get("points", {})
    post_points = post.get("points", {})
    # Means are recomputed over the ID intersection only — a set-size
    # change (created/deleted Points) must not shift the mean past the
    # ceiling (review P2: full-set means can drift on add/remove alone).
    common = set(pre_points) & set(post_points)
    if not common:
        # Zero shared Point ids (total replacement): both recomputed means
        # are 0.0, so the ceilings would trivially pass — a vacuous pass
        # (pre 0.90 → post 0.30 reported True before this fix). Fail closed
        # with an explicit signal instead of a silent 0.0-delta pass.
        return {
            "passed": False,
            "reason": "no_common_points",
            "overlap": 0,
            "mean_abs_delta": 0.0,
            "max_point_abs_delta": 0.0,
            "mean_ceiling": max_mean_drift,
            "point_ceiling": max_point_drift,
            "pre_count": pre.get("count", 0),
            "post_count": post.get("count", 0),
        }
    pre_mean = sum(float(pre_points[pid]) for pid in common) / len(common)
    post_mean = sum(float(post_points[pid]) for pid in common) / len(common)
    mean_abs_delta = abs(post_mean - pre_mean)
    max_point_abs_delta = max(
        (abs(float(post_points[pid]) - float(pre_points[pid])) for pid in common),
        default=0.0,
    )
    passed = (
        mean_abs_delta <= max_mean_drift
        and max_point_abs_delta <= max_point_drift
    )
    return {
        "passed": passed,
        "mean_abs_delta": mean_abs_delta,
        "max_point_abs_delta": max_point_abs_delta,
        "mean_ceiling": max_mean_drift,
        "point_ceiling": max_point_drift,
        "pre_count": pre.get("count", 0),
        "post_count": post.get("count", 0),
        "overlap": len(common),
    }
