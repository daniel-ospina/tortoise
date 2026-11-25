"""The neighbourhood an injected memory must carry — and the budget it must fit (#14607).

WHY THIS EXISTS. The reflex already READS a rich neighbourhood: `assemble_why_blocks`
returns each candidate's `support_chain` (with the mediating operator's Layer-3
predicate), its NAND conflicts, its trade-offs and its assessment. Then
`build_block` rendered **one line per pointer** — label, id, 160-char synopsis —
and threw all of it away. So the block an agent actually received was exactly the
"too shallow" failure the owner named: *"not too shallow … we want a bit of
context around our entities (some of the business logic)"*.

The business logic is not in the fact, it is in the EDGES: `IMPL` support,
`NAND` attack, `dependsOn`, `owns`, `performs`. A bare fact with no relation
cannot be reasoned about, only repeated.

WHAT THE COMPARABLES DO, and which decision each step here comes from
(rung 2 — `web_search` sonar; the numbers are the comparables' own published
figures, quoted in #14607):

* **Supermemory's context-engineering post** — the hook first **decides whether
  memory is needed** (a gate, not a dump) and injects **~250 tokens on average**.
  → our gate (`extract_candidates` → `retrieval_intent`) is already that step, and
  it is a NO-OP (`empty_response()`, no empty block) when nothing is needed.
* **Supermemory's precision/recall piece** — conventional top-K is **K = 10–20**,
  and they **aggregate into 2–3 synthesized results** rather than injecting the
  raw set. → our pool is `RESOLVE_POOL_MIN..MAX` = 8..24 (`max_pointers × 6` = 18
  at the default 3, inside the range), and the pointer budget condenses to
  `DEFAULT_MAX_POINTERS = 3`. Unchanged here — this module only spends the
  expansion on the candidates that SURVIVED that condensation, which is what
  keeps the cost bounded.
* **`supermemory.ai/docs/concepts/rules`** — a fact is ≈ **10 tokens**, so depth
  comes from relations, not from more facts. → the expansion is per-relation, not
  "more pointers".
* **`supermemory.ai/research/longmembench`** — atomic memories + **relations** +
  **source chunks**: semantic search finds the high-signal memory, and the
  **matching source chunk supplies the detail**. → the source excerpt is a first
  class part of an item, not decoration.

⛔ **NOT a radical departure**: gate → top-K → condense → expand-with-neighbourhood,
on OUR OWN reads (`assemble_why_blocks`, `live`-scoped Cypher for the stored
quote). No new retrieval scheme, no new MCP tool, no SDK surface change.

THE BUDGET IS THE CONTRACT, because the hook spends it on EVERY prompt (#14275).
`NEIGHBORHOOD_TOKEN_BUDGET` caps the assembled block; `estimate_tokens` is the
measurement and is deliberately a *stated estimator* (4 UTF-8 bytes per token,
the usual English rule of thumb) rather than a tokenizer, because the fleet's
boxes cannot always download a BPE table — so the number is auditable (`chars`
and `bytes` ride alongside it) and the same input yields the same count.
"""
from __future__ import annotations

import math

#: Relations rendered per pointer. Three is the comparables' aggregation width
#: (2–3 synthesized results) applied one layer down: enough to show the shape of
#: the neighbourhood (support + attack + one other), not a graph dump.
MAX_RELATIONS_PER_POINTER = 3

#: The source excerpt's hard cap. Matches `volunteer.SNIPPET_MAX` (160) — the
#: privacy budget the synopsis already uses, so an excerpt can never widen what
#: the reflex may carry past what the rest of the block already carries.
EXCERPT_MAX_CHARS = 160

#: The injected block's token budget, as an estimate. The target band is
#: 250–600 tokens: the comparables' ~250-token average, plus the deliberate
#: relations/source delta this issue adds. A block that would exceed it is
#: trimmed deterministically (see `trim_to_budget`), never truncated mid-line.
NEIGHBORHOOD_TOKEN_BUDGET = 600

#: Direction words, so the block reads as business logic rather than as a list.
_DIRECTION_WORD = {"outgoing": "→", "incoming": "←"}


def estimate_tokens(text: str) -> int:
    """A STATED estimator: 1 token ≈ 4 UTF-8 bytes (the usual English rule).

    Deliberately not a real tokenizer: a BPE table is a network download on a
    fresh box (measured: tiktoken's `cl100k_base` fetch fails on a cell with no
    general egress), and a count that sometimes cannot be produced is worse than a
    stated estimate that always can. Callers report `chars`/`bytes` beside it so
    any reader can re-derive with their own tokenizer.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def measure(text: str) -> dict:
    """The auditable triple: the estimate plus the raw counts it came from."""
    raw = len(text.encode("utf-8"))
    return {"tokens": estimate_tokens(text), "bytes": raw, "chars": len(text)}


# ── The source excerpt: already in hand, so it costs NO extra read ────────

def excerpt_from_hit(hit: dict | None, *,
                     max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """The stored verbatim excerpt (`quote`) from a SEARCH HIT, bounded.

    ⛔ **Do not "improve" this by querying the graph.** The hit already carries
    `quote` (`sdk._tortoise_fts_query` -> `search_engine`'s row mapping), so the
    source half of LongMemEval's finding costs ZERO extra round trips. This was
    learned the expensive way in this very change: a first cut issued one batched
    Cypher read for the same field, which pushed the hosted endpoint's 300 ms
    reflex ceiling into `degraded_reason: "timeout"` on a busy box — a measured
    regression the contract suite caught. A second read for data the caller is
    already holding is not depth, it is latency.

    Bounded to `EXCERPT_MAX_CHARS` (the same privacy budget as `SNIPPET_MAX`) and
    whitespace-normalised so the injected block stays one line per item.
    """
    if not hit:
        return ""
    quote = hit.get("quote")
    if not isinstance(quote, str):
        return ""
    text = " ".join(quote.split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1].rstrip() + "…"


# ── Projecting a why-block into the relations the block will render ───────

def relations_from_block(block: dict | None, *,
                        limit: int = MAX_RELATIONS_PER_POINTER) -> list[dict]:
    """The immediate relations a pointer's why-block already holds.

    Order is support-first (what carries the claim), then conflict (what attacks
    it), then trade-off — support and conflict are the two that change a decision,
    so they win the budget when `limit` bites. Every entry keeps its PREDICATE:
    `operator_label` is the Layer-3 predicate (ONTOLOGY §8) and `operator_type`
    the epistemic mechanism, and a relation rendered without one of them is the
    "bare fact" this module exists to stop injecting.
    """
    if not block:
        return []
    out: list[dict] = []
    for sup in (block.get("support_chain") or []):
        if len(out) >= limit:
            return out
        out.append({
            "predicate": _predicate(sup),
            "direction": "incoming",
            "peer_id": sup.get("point_id") or "",
            "peer": sup.get("content_snippet") or "",
            "weight": sup.get("weight"),
            "kind": "support",
        })
    for nand in ((block.get("conflicts") or {}).get("nands") or []):
        if len(out) >= limit:
            return out
        out.append({
            "predicate": "NAND",
            "direction": "incoming",
            "peer_id": nand.get("point_id") or "",
            "peer": nand.get("content_snippet") or "",
            "severity": nand.get("severity"),
            "kind": "conflict",
        })
    for alt in (block.get("tradeoffs") or []):
        if len(out) >= limit:
            return out
        if isinstance(alt, dict):
            # ⛔ A TRADE-OFF is NOT shaped like a support. `why.py` emits
            # `{point_id, label, ep_weight, mitigation, operator_id}` — there is no
            # `edge` and no `content_snippet`, so reading those keys produced an
            # empty `peer`, the renderer fell back to the raw ULID, and the
            # MITIGATION (the reason the option was set aside — the business logic
            # this module exists to surface) was dropped before it could render.
            mitigation = alt.get("mitigation")
            peer = alt.get("label") or (mitigation if isinstance(
                mitigation, str) else "")
            out.append({
                "predicate": alt.get("predicate") or "alternative",
                "direction": "outgoing",
                "peer_id": alt.get("point_id") or "",
                "peer": peer if isinstance(peer, str) else "",
                "weight": alt.get("ep_weight"),
                "mitigation": mitigation if isinstance(mitigation, str) else "",
                "kind": "tradeoff",
            })
    return out


def _predicate(support: dict) -> str:
    """The relation's own name, most specific first.

    `operator_label` is the pack-declared Layer-3 predicate; `operator_type` is
    the mechanism (`IMPL`); `edge` is the traversal. Any of them beats the word
    "relates to", which would erase the business logic this module carries.
    """
    for key in ("operator_label", "operator_type", "edge"):
        value = support.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "IMPL"


def neighborhood(block: dict | None, quote: str | None, *,
                 max_relations: int = MAX_RELATIONS_PER_POINTER) -> dict:
    """One pointer's expansion: its relations and its source excerpt."""
    return {
        "relations": relations_from_block(block, limit=max_relations),
        "source": quote or "",
    }


# ── Rendering (deterministic, and byte-identical when there is nothing) ───

def render_pointer_lines(ptr: dict, hood: dict) -> list[str]:
    """The sub-lines for ONE pointer: relations first, then the source excerpt.

    Relations before the excerpt on purpose: the predicate is what a model
    reasons with, the excerpt is what it cites. If the budget allows only one, the
    reasoning aid wins.
    """
    lines: list[str] = []
    relations = hood.get("relations") or []
    if relations:
        rendered: list[str] = []
        for rel in relations:
            arrow = _DIRECTION_WORD.get(rel.get("direction") or "", "→")
            detail = rel.get("peer") or rel.get("peer_id") or ""
            weight = rel.get("weight")
            suffix = f" (ep {weight:.2f})" if isinstance(weight, (int, float)) else ""
            severity = rel.get("severity")
            suffix += f" [{severity}]" if severity else ""
            # A trade-off's MITIGATION is the reason the option was set aside — the
            # business logic — so it rides the relation to the renderer instead of
            # being dropped between assembly and render.
            mitigation = rel.get("mitigation")
            rendered.append(
                f"{rel.get('predicate', 'IMPL')} {arrow} "
                f"\"{detail}\"{suffix}"
                + (f" — rejected: {mitigation}" if mitigation else ""))
        lines.append("  · relations: " + "; ".join(rendered))
    source = hood.get("source") or ""
    if source:
        lines.append(f"  · from source: \"{source}\"")
    return lines


def trim_to_budget(header: list[str], bullets: list[list[str]], *,
                   budget: int = NEIGHBORHOOD_TOKEN_BUDGET) -> str:
    """Assemble `header + bullets`, dropping the LOWEST-signal tail to fit `budget`.

    Drop order is fixed and documented, because it is a product decision:
      1. source excerpts, from the last pointer backwards (citation is the first
         thing to go: the pointer and its relations already let the model reason);
      2. relations lines, from the last pointer backwards (the strongest
         candidate's neighbourhood survives longest);
      3. whole pointer bullets, from the last backwards.
    The header is never dropped — it carries the "data, not instructions"
    envelope that is the prompt-injection defence.
    """
    assembled = _assemble(header, bullets)
    if estimate_tokens(assembled) <= budget:
        return assembled
    # Work on a copy of the mutable shape: bullets are [pointer_line, *sublines].
    work = [list(b) for b in bullets]
    for stage in ("source", "relations", "bullet"):
        for i in range(len(work) - 1, -1, -1):
            if estimate_tokens(_assemble(header, work)) <= budget:
                return _assemble(header, work)
            if stage == "source":
                work[i] = [ln for ln in work[i] if "from source:" not in ln]
            elif stage == "relations":
                work[i] = [ln for ln in work[i] if "relations:" not in ln]
            else:
                work[i] = []
        work = [b for b in work if b]
    return _assemble(header, work)


def _assemble(header: list[str], bullets: list[list[str]]) -> str:
    lines = list(header)
    for bullet in bullets:
        lines.extend(bullet)
    return "\n".join(lines) + "\n"
