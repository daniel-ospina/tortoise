---
title: "Implementation Plan — #2004 (W8): Builder capability catalog"
type: engineering
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-09-02
aboutSubjects: tortoise
aboutObjects: tortoise
---

# 2026-09-02 — #2004 (W8): Builder capability catalog — implementation plan

**Level:** project (epic child #1976) · **Complexity:** standard
**Branch:** feat/2004-W8-onboarding (base: main @ d4b65da3 — W1/W2/W4/W5 merged)
**Scope anchor:** issue #2004 body + epic docs/epics/2026-08-29-agent-driven-onboarding-1976/06-plan.md (WF-6, DM-5, I-7, DE2E-9) + 04-test-design.md surface 13.

---

## 1. Objective (restated)

Ship the pullable builder capability catalog:
1. **Registry** — extend `tortoise/tool_registry.py` (R2-9: no new infra) with an indexers+extractors capability registry (`CAPABILITY_CATALOG`) and a read accessor.
2. **Endpoint** — `GET /v1/capabilities` (epic I-7 contract: `200 {modules: [{name, kind: indexer|extractor, description}]}`), hosted in `hosted_api.py` (hand-written dual-auth route — the onboarding-state endpoint precedent).
3. **Presented once on the build path** — dashboard replaces the W1 static placeholder SOURCE with the registry-backed catalog (fetched on first build-fork render); the `catalog-presented` step-edge write mechanism is UNCHANGED (W1/W5 own it — this W does not add a second mark or any W11 telemetry). Presentation is a nudge, never a billing gate (R2-7).
4. **W8b module-note sweep** — every extractor/indexer module named by the registry carries a code-level docstring note (DM-5 wording) referencing the catalog.

**Indicators:** endpoint returns the accurate list; catalog-presented step-edge set once per build org; every swept module carries the note (inventory test); no new infra.

## 2. Design decisions

### D1 — Catalog data model (`tortoise/tool_registry.py`)

New frozen dataclass + module-level constant + accessor (same file as `ToolDefinition`; nothing new to run):

```python
CATALOG_NOTE = (
    "This module is referenced in the builder capability catalog (onboarding) — "
    "tortoise/tool_registry.py CAPABILITY_CATALOG. If you add or rename an "
    "extractor/indexer, update the catalog reference."
)

@dataclass(frozen=True)
class CatalogModule:
    name: str          # canonical builder-facing name (presented copy)
    kind: str          # "indexer" | "extractor"
    description: str   # builder-facing one-liner
    modules: tuple[str, ...]  # real code module(s) implementing it (W8b note homes)
    available: bool = True    # False = planned/future (document extractor)

CAPABILITY_CATALOG: tuple[CatalogModule, ...] = ( ... 4 entries ... )

def capability_catalog() -> list[dict]:
    """Pullable catalog rows — {name, kind, description, available}."""
```

**The 4 canonical modules** (names/kinds/descriptions preserved from W1's presented placeholder so dashboard copy + DE2E-9 text assertions hold):

| name | kind | description | modules (real homes) | available |
|---|---|---|---|---|
| Session recorder | indexer | Files agent conversations to the graph. | `tortoise/sdk.py` (`TortoiseSDK.capture_session` facade), `tortoise/hosted_api.py` (hosted `POST /v1/sessions` capture) | True |
| Session extractor | extractor | Pulls decisions and findings out of recorded sessions. | `tortoise/extractor.py` (Extractor/LLMExtractor), `tortoise/extractor_v2.py` (5-stage pipeline v2), `tortoise/session_indexer.py` (session-file metadata extraction) | True |
| Document indexer | indexer | Indexes documents you point your agent at. | `tortoise/ingest.py` (corpus ingestion), `tortoise/file_indexer.py` (file-identity primitives), `tortoise/session_indexer.py` (session .md indexing) | True |
| Document extractor | extractor | Extracts claims and decisions from indexed documents (planned). | — (future) | False |

**Accuracy rationale (evidence):** repo-wide class/module search (`class *Indexer` / `class *Extractor`, `session_indexer`, `file_indexer`, `ingest`, capture_session) produced exactly these implementing modules. The GitHub indexers (`tortoise/indexer/github_indexer.py`, `github_docs.py`) are NOT catalog modules: they are the self-use Settings memory-source surface (W4: github_* toggles; epic boundary: "Webhook-based GitHub ingestion lifecycle … NOT in this epic's workstreams") — build-fork orgs see the catalog, not GitHub toggles. Documented in the registry comment block so the next module add/rename lands in the right place.

### D2 — Endpoint (`tortoise/hosted_api.py`)

`GET /v1/capabilities` — hand-written route modeled on `GET /v1/onboarding/state`: `Depends(get_current_team_session_ungated)` (dual-auth; any authed team context — the catalog is org-independent static registry data), returns the accessor rows. **No MCP tool** (I-7 contract is the HTTP read; adding a tool would force `GROUP_BY_NAME` + `mcp_server.py` handler wiring for zero required surface). No graph touch → never 'unavailable'.

### D3 — Dashboard (W1's placeholder SOURCE replaced)

- `wizardFlow.js`: `BUILD_CATALOG_PLACEHOLDER` becomes the **offline fallback** (shape/names unchanged — the endpoint contract test pins them); add a pure helper:

```js
// resolves the registry-backed catalog; falls back to the static list when
// the endpoint is unreachable (honest offline degrade — same names).
export function resolveBuildCatalog(modules, fallback = BUILD_CATALOG_PLACEHOLDER) {
  return (Array.isArray(modules) && modules.length > 0) ? modules : fallback
}
```

- `main.jsx` (wizard step-2, build branch): fetch `GET /v1/capabilities` once per session (ref latch mirroring `catalogMarkedRef`), store in state; the existing catalog render div consumes `resolveBuildCatalog(wizardCatalog)`. Fallback covers endpoint-down. Update stale copy ("(preview until the full catalog ships)" → shipped). The catalog-presented checkpoint effect + handler marks are untouched (no second mark; keyed-MERGE makes replay a no-op — once-only already holds).

### D4 — W8b sweep (docstring notes)

Append the DM-5 note sentence to the **module docstrings** of every module listed in `CAPABILITY_CATALOG[].modules` (7 unique files: sdk.py, hosted_api.py, extractor.py, extractor_v2.py, session_indexer.py, ingest.py, file_indexer.py). Each note names its catalog module(s) + points at `CAPABILITY_CATALOG`. The future Document extractor cannot host a file note — its registry entry is the note (available=False). Inventory is TEST-verified (D6).

**W8b sweep count: 7 module files (+ the registry entry for the future module = 4/4 catalog modules covered).**

### D5 — No telemetry

`catalog-presented` is a node checkpoint with NO W11 event (per issue pins). This W writes no analytics events.

## 3. Integration surface map (test-design #1992 surface 13 + repo surfaces)

| # | Surface | Type | Data flow | Test layer | Contract | Key failure modes |
|---|---|---|---|---|---|---|
| 1 | tool_registry `CAPABILITY_CATALOG` | State | Out | Unit | 4 modules; kinds ⊆ {indexer, extractor}; available flag; every `modules` path exists | Registry stale / typo'd path |
| 2 | `GET /v1/capabilities` | API (HTTP) | Out | Contract (docker-lane TestClient) | `200 {modules:[{name,kind,description,available}]}`; names = canonical set; total 4 | 404/empty; drift vs presented names |
| 3 | catalog-presented gate (build fork) | State/graph | Out | Integration (docker lane) | build = harness-connected + first-points-filed + catalog-presented → `status: complete`; decide alone never completes build; replay no-op | gate not evaluable; re-present |
| 4 | dashboard build-catalog fetch + fallback | UI/state | In | Unit (node --test, pure helper) + hosted-e2e (existing text assertions) | fetched modules render; fallback identical names; once-only mark preserved | fetch race re-presents; endpoint-down blank catalog |
| 5 | W8b module-note inventory | Filesystem | Out | Unit | every file in `modules` tuple has module docstring containing the note + its catalog name | module added w/o note; sweep missed file |

**Bug-pattern flags:** silent function skips — the dashboard fetch must keep the fallback render path (never a blank catalog when the endpoint is down); conditional guards — catalog fetch + present-mark both fire only on build fork step-2 render, guarded per team; N+1 — single fetch per session (ref latch), no per-module calls.

**Checklist notes:** endpoint empty-shape (`[]` vs null) handled by `resolveBuildCatalog`; module-name drift pinned by contract tests on BOTH sides (py endpoint test + JS fallback test); hosted-e2e DE2E-9 text assertions (`Session recorder` etc. in tests/e2e/test_dashboard_onboarding.py) remain valid because names are unchanged — W8 runs those suites in CI; not runnable locally here (no hosted-e2e harness).

## 4. Implementation steps

1. **tool_registry.py**: add `CatalogModule`, `CATALOG_NOTE`, `CAPABILITY_CATALOG` (4 entries, mapping above), `capability_catalog()` accessor. Place after the registry groups section (module tail) with a comment block documenting the catalog-module ↔ code-module mapping + why GitHub indexers are excluded.
2. **hosted_api.py**: add `GET /v1/capabilities` route (near the onboarding state endpoints) — docstring mirroring the epic I-7 pin; body imports `tool_registry.capability_catalog`.
3. **W8b sweep**: append module-docstring notes to `tortoise/sdk.py`, `tortoise/hosted_api.py`, `tortoise/extractor.py`, `tortoise/extractor_v2.py`, `tortoise/session_indexer.py`, `tortoise/ingest.py`, `tortoise/file_indexer.py`.
4. **Dashboard**: `wizardFlow.js` — placeholder→fallback comment + `resolveBuildCatalog` helper; `wizardFlow.test.js` — add helper test + keep placeholder-shape test. `main.jsx` — catalog state + one-shot fetch effect + render uses helper; stale-copy sweep ("preview until the catalog ships").
5. **Tests**:
   - `tests/test_capability_catalog.py` (lane-agnostic unit): registry shape (4 modules, canonical names/kinds, one future), every `modules` path exists on disk, module-note inventory (each file's docstring carries note + catalog name), endpoint-shape pre-contract (accessor rows).
   - `tests/test_capabilities_endpoint.py` (docker-lane, module-level skip when `TORTOISE_DB_URI` unset): TestClient register → `GET /v1/capabilities` 200 shape + names + total; build-fork gate via checkpoint (`fork: build` → `harness-connected` → `first-points-filed` → `catalog-presented` → status complete; decide-completed alone stays active; replay of catalog-presented = 200 no-op).
   - Register both files under the `onboarding` surface in `config/ci-surfaces.yml`. Add ROUTED_NAMESPACES entries only if a 'registry' namespace literal appears (helpers use TestClient register + `_make_sdk(namespace=team_id)` → likely none; verify with markers gate).
6. **Dashboard dist rebuild** (`vite build` in website/apps/dashboard) — committed dist is the served artifact (dashboard-js-tests + hosted-e2e read it).
7. **Verify**: docker-lane pytest (registry + endpoint files, plus the wider onboarding set), carve-out pytest (unit file), `node --test` (wizardFlow), `uv run ruff`, markers gate, typecheck if touched signatures (none).

## 5. Test count estimate

py: ~10-14 (registry ~6, endpoint ~5 incl. gate + replay, inventory ~3) · JS: +1 helper test · existing suites untouched (names preserved).

## 6. Merge-collision notes (parallel Ws)

- `tortoise/tool_registry.py` is SHARED → full-matrix CI. W3 (#2156, OPEN) adds `tortoise_onboarding_seed` ToolDefinition + GROUP_BY_NAME lines in the same file — if W3 merges before W8, expect a small conflict on the onboarding group block (resolve on rebase; do not pre-emptively rebase onto W3).
- `tortoise/hosted_api.py` is touched by W7 (#2168, OPEN — invites/sessions) + W6 (in flight — sessions DELETE). W8 touches ONLY: a new `GET /v1/capabilities` route + module-docstring note at the file head. Do NOT touch W6/W7 invite/session code.
- Dashboard Settings/main.jsx is touched by W6/W7 in parallel — W8's main.jsx edits are confined to the wizard step-2 catalog block + wizard state area.
- Guardrails: no git in the hub main checkout; git only via this worktree; hub main is dirty.

## 7. Out of scope (explicit)

- No MCP tool for the catalog; no `GROUP_BY_NAME` addition; no W11 telemetry; no hosted-e2e edits in this PR (existing DE2E-9 text assertions keep passing — names unchanged); no GitHub indexer catalog entries; no billing/plan coupling (R2-7 — presentation only).
