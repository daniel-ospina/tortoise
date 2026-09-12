---
title: "#3011 Context-Assembly Experiment — Implementation Plan"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-11
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

<!-- spec-path: docs/experiments/2026-09-11-abc-context-assembly-experiment.md -->
<!-- issue: https://github.com/daniel-ospina/tortoise/issues/3011 -->

# Implementation plan — #3011 context-assembly experiment

**Spec (frozen, authoritative):** `docs/experiments/2026-09-11-abc-context-assembly-experiment.md`
**Issue:** #3011 · **Branch:** `feat/3011-context-assembly-impl` · **Related:** #2978/#3000 (run blocker), #2598 (draft-default), #2976

This plan freezes the **interfaces** so implementation tracks can proceed in parallel. If the spec and this plan disagree, **the spec wins**.

---

## 0. Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Arm B confidence source | **Promote drafts → live, then run EP** | The eval graph is entirely `status:"draft"` at 5 ingest sites → `ep._live_only` skips everything → every claim reads a fake `0.50`. #2598: drafts must never show `0.5` as if measured. |
| Unmeasurable confidence renders as | **`confidence: unmeasured`** | #2598: drafts are EP-inert — "don't fake measurement". A promoted point that still has no EP state must not print `0.50`. |
| Relation wording | `IMPL`→`IMPLIES`, `NAND`→`CONTRADICTS` | Spec §3.1 |
| Supersession render | `C1 [SUPERSEDED BY C2]`, derived by following `CORRECTS` | There is no `superseded_by` property |
| Serializer path (spec left it UNSPECIFIED) | `tortoise/subgraph.py` (engine) + `tortoise/subgraph_render.py` (serializer) | Spec requires path + git sha in the manifest |

**Ontology map (must be respected):**
- Claims are `:Point` with `content`; **operator nodes are also `:Point`** (`is_operator:true`) and have **no `content`** — they must be traversed *through*, never rendered as claims.
- `IMPL`/`NAND` edges run operator→**both** endpoints; `r.idx = 0` is the source, `idx > 0` the targets. A bare edge walk returns operator nodes — filter `other.is_operator = false`.
- Supersession edge is **`CORRECTS`** (`(new)-[:CORRECTS]->(old)`), not a property.
- Entity link is `(p)-[:aboutObject]->(o:Object)`; the display text is `o.name`.
- EP posterior read is the canonical coalesce: `coalesce(p.posterior_alpha, p.ep_alpha, 1.0) / (… + coalesce(p.posterior_beta, p.ep_beta, 1.0))`.

---

## 1. Frozen interfaces

### 1.1 `tortoise/subgraph.py` — traversal + ranking (Track A)

```python
EDGE_PRIORITY_WEIGHT = {"aboutObject": 1.0, "supersession": 0.9, "NAND": 0.8, "IMPL": 0.7}
HOP_DECAY = {1: 1.0, 2: 0.5}
SEED_LIMIT = 64      # vector_search fetch; tie-break must see the full tied set
SEED_COUNT = 8       # selected seeds
PER_ANCHOR_CAP = 12  # AFTER reserved slots

@dataclass(frozen=True)
class Candidate:
    point_id: str
    content: str
    anchor_id: str
    edge_type: str            # aboutObject | supersession | NAND | IMPL
    hop: int                  # 1 | 2
    s_norm: float             # min-max over the 8 SELECTED seeds
    raw_score: float          # s_norm * weight * decay  (pre-damp)
    score: float              # post-damp (authoritative for admission)
    damped: bool
    reserved: bool            # supersession/validity or NAND endpoint

@dataclass(frozen=True)
class Relation:
    source_id: str
    relation: str             # aboutObject | supersession | NAND | IMPL
    target_id: str
    target_label: str         # entity name for aboutObject, else ""

@dataclass(frozen=True)
class Subgraph:
    seeds: tuple[tuple[str, float], ...]   # SELECTED seeds in rank order
    anchors: tuple[str, ...]               # ordered anchors (seed order)
    candidates: tuple[Candidate, ...]      # post cap->dedupe, admission order
    relations: tuple[Relation, ...]
    zero_seed: bool
    reserved_overflow: int                 # count of dropped reserved lines
    seed_fn: str                           # "vector" | "bm25"

def normalize_seeds(seeds: list[tuple[str, float]]) -> list[tuple[str, float]]: ...
def select_seeds(candidates: list[tuple[str, float]], n: int = SEED_COUNT) -> list[tuple[str, float]]: ...
def rank_score(s_norm: float, edge_type: str, hop: int, *, hub_degree: int | None) -> float: ...
def build_subgraph(sdk, question: str, *, namespace: str | None = None) -> Subgraph: ...
```

**Rules (frozen):** min-max over the 8 selected seeds (all-equal ⇒ every `s_norm = 1.0`); exactly 1 hop; a 2nd hop **only** through an `aboutObject` hub; hub damping `/(1 + ln(1 + whole_graph_degree))` for 2-hop only; **cap → dedupe** (dedupe by point `id`, keep highest score; score decides, edge priority is the tie-break; then lower `id`); no separate global cap; in-network before out-of-network; greedy to the word budget.

### 1.2 `tortoise/subgraph_render.py` — serializer + union packer (Track B)

```python
def render_arm_b(sg: Subgraph, *, question_date: str | None, haystack_session_ids: list[str],
                 session_meta: dict[str, str], word_budget: int) -> str: ...
def render_arm_c(sg: Subgraph, *, turns_by_point: dict[str, list[str]], ...) -> str: ...
EMPTY_CONTEXT_SENTINEL = "[no context retrieved]"
```

**Block order (frozen):** claim line → reserved supersession/NAND lines → other relation lines in priority order → provenance line → **(arm C only)** verbatim turns → `confidence:` last.
**Fallbacks:** `session ?`, `turn ?`, `(date unknown)`. **Confidence:** `f"{c:.2f}"`, or `unmeasured` when no EP state.

### 1.3 Reader seam (Track D)
`LLMReader.answer` calls `render_context` internally. Tracks B/C emit **text**, not hits. Add a pre-rendered-evidence path reusing `tortoise/reader.py:292 build_reader_user_message(evidence, question)`.

---

## 2. Tracks

| # | Track | Depends on | Deliverable |
|---|---|---|---|
| **A** | Subgraph engine | — | `tortoise/subgraph.py` + hermetic tests over a fake graph |
| **C** | EP activation | — | promote drafts→live + run EP in the eval pipeline; `unmeasured` fallback |
| **D** | Reader seam | — | pre-rendered-evidence reader path + test |
| **B** | Serializer + union packer | A | `tortoise/subgraph_render.py` + golden-text tests |
| **E** | Arm runner + metrics | A,B,C,D | 4-arm driver, metrics 5/8/9, gold-evidence artifact builder |
| **F** | Leakage tests | A,B | perturbation test + static reference assertion (hard stop) |

**Wave 1 (parallel): A, C, D.** **Wave 2: B** (needs A's shape), **F** (needs A,B). **Wave 3: E.**

---

## 3. Validation-before-benchmarking (mandatory)

Per the standing directive: **small validation tests before any benchmark run.**
1. Hermetic unit tests per module (no DB, no model) — Track A/B/D.
2. Docker-lane integration test: real graph, small fixture, assert the rendered block matches a golden string.
3. §10 stage-6 validation gate on `gpt4_4929293a` — **only after #3000 lands.**
4. §9.5: the gold-evidence artifact must exist, be validated, committed, sha256-recorded — **before the first render.**

**Do NOT run the 52-question benchmark until 1–4 pass.**
