<!-- research-path: docs/architecture/STORAGE-ARCHITECTURE.md (see ### Pattern Research — documented skip) -->

# #4997 — the vector leg's served label set, and the store's indexed label set, made facts the system holds

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Give the vector leg's served label set one written definition the query path reads, and make the store's vector-indexed label set a measured, reported fact — so the gap between them is knowable without hand measurement. **Change no index and assert no equality between the two sets.**

**Team:** epistemic-team
**Issue:** daniel-ospina/tortoise#4997 · **Branch:** `fix/4997-vector-index-parity` · **Tier:** Standard (low-medium → 2 plan reviewers + Reviewer #5, max 3 cycles)

**Architecture:** **No new module.** The `entity_type → graph label` contract goes into `tortoise/security.py`, beside `VALID_ENTITY_TYPES` — that module already *declares itself* "the single home for the genuinely shared security semantics: Cypher identifier validation (filter keys, relationship types, **entity types**)".
**Why the edge is acyclic — stated correctly:** the reason is **what `security.py` imports**, not who imports it. `security.py` imports only `os`/`re`/`pathlib` and nothing from `tortoise`, so it is a true leaf; and `tortoise/projection/__init__.py:5358` **already** imports it (`from tortoise.security import validate_rel_type`, function-scoped), which is the working precedent the new consumer follows. `tortoise/search_engine.py` has no `security` import today and will add one. `run_vector_query` reads the mapping instead of deriving a label per call. `FalkorProjection` gains a fail-open `_record_vector_index_inventory()` that reads the engine's **real** `CALL db.indexes()` rows and records which of the declared labels carry a VECTOR index. **Index creation is untouched.** The change is deliberately V1-neutral: it reports the relationship (which is V1's content) rather than acting on it.
**Tech Stack:** Python 3.12, FalkorDB (`CALL db.indexes()`), pytest. No new dependency, no `uv.lock` change.

> **Why the mapping is the GENERAL contract, not a vector-leg one.** `#5407` (filed from this issue's scoping) records **six** places that derive a graph label from an `entity_type`, and `#5404`'s fix direction (b) is *"derive the label from a single declared `entity_type → label` mapping"*. This plan declares that mapping in the module that already owns the vocabulary; the vector leg is its **first consumer**, and `#5407` migrates the other three query legs onto it. The mapping is therefore named for the contract (`ENTITY_TYPE_LABELS` / `entity_label`), not for the leg that happens to consume it first.

### Pattern Research

> **Findings date:** 2026-09-25

**Library docs (preflight) —** no third-party deps introduced. The only engine surface touched is `CALL db.indexes()` (already used at `tortoise/projection/__init__.py:6112`). No new library, no version change.

> **Gate skipped: zero third-party dependencies in the plan.** Per `writing-plans` §Skip Rules the multi-call Perplexity gate does not apply — no library, no proprietary SDK, in-repo `self.g.query` exclusively. **Step A ran**: the scoping artifact's `### Axis Research` / `### Integration Docs` blocks were consumed (the architecture axis was deduplicated against `docs/architecture/STORAGE-ARCHITECTURE.md` §12.1b/§12.1c and `docs/research/2026-09-23-vector-index-ram-model/research-brief.md`), and the engine facts below are **measured**, not researched.

| engine probe (docker `falkordb/falkordb:latest`, graph `vecindex_probe_4997`) | result |
|---|---|
| `CREATE VECTOR INDEX IF NOT EXISTS FOR (p:Point) ON (p.embedding) OPTIONS {…}` | `Invalid input 'I'` — **the syntax does not exist on this engine** |
| the same `CREATE VECTOR INDEX` a second time | `Attribute 'embedding' is already indexed` — **raises; does not rebuild** |
| `CALL db.idx.vector.createNodeIndex('Point','embedding',4,'HNSW')` | `Procedure … is not registered` |
| `CALL db.idx.vector.queryNodes('Event',…)`, **no `Event` index** | `Invalid arguments for procedure` — **the same error as a signature mismatch** |
| `CALL db.indexes()` row shape | 9 columns; `[1]` = properties, `[2]` = **`types`** `{'embedding': ['VECTOR']}` / `{'content': ['FULLTEXT']}` |

**Library version & API surface / Idiomatic usage / Pitfalls** — not applicable (internal callers only; existing in-repo pattern, 2+ examples at `projection/__init__.py:6100-6130` and `:6340-6375`). The engine footguns are covered by measurement above.

**⚠️ Where the `db.indexes()` row-shape normalization already lives (recorded, not silently re-implemented).** This plan adds a **fifth** reader of those rows, so the contract is named rather than implied:

| reader | what it reads | why this plan's differs |
|---|---|---|
| `tortoise/projection/__init__.py:6112` (embedded composite repair) | `_row[0]` label + `_row[1]` properties, substring match | **cannot** tell VECTOR from FULLTEXT — it never looks at column 2 |
| `tortoise/hosted_backup.py:1544` `_index_has_is_operator()` | `_row[1]`, **exact field name, never substring** (its docstring: substring *"would also match an unrelated property such as `is_operator_flag`"*) | needs no type discrimination |
| `tests/test_indexes.py:82-99` `_range_indexes()` | normalizes **both** 3.x flat rows and 4.x dict rows to `{label: {field: [types]}}` | test-only; the 3.x path is unreachable here (see the gate below) |
| `tests/test_divergence_conformance.py:133-146` | a second copy of the above | test-only |
| **this plan** `_record_vector_index_inventory()` | label + **column 2 `types`**, VECTOR membership | it is the only reader that must discriminate index TYPE, which is why it reads a column none of the others touch |

**Verdict recorded: `unify-contract-keep-drivers`.** The five readers do not share a shape today and are not unified by this plan (that would touch `hosted_backup.py` plus two test helpers — outside this lane). The contract they *do* share — column 0 = label, column 1 = properties, **column 2 = types**, where the 3.x shape is flat — is written down in this table and in the implementation's docstring. ⚠️ **`#5407` does NOT cover this**: its scope is label *sets*, not the engine's row shape.

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---|---|---|---|---|---|
| 1 | `tortoise/security.py` — `ENTITY_TYPE_LABELS` / `entity_label()` | pure logic | internal | **Unit** | `dict[str,str]`; `entity_label(str) -> str` | unknown-but-`str` → `capitalize()`; **non-`str` → the SAME exception type as today (`AttributeError`)**, see below |
| 2 | `run_vector_query` ← the declaration | internal call | In | **Unit** (fake graph capturing the emitted Cypher) | the emitted label came **from the declaration** (provenance, not value) | a re-derived label (the drift) — the **M1 mutation target** |
| 3 | `_record_vector_index_inventory()` ← `CALL db.indexes()` | **DB read** (external engine) | In | **Unit** + **Integration** (live docker) | rows: `[0]`=label, `[2]`=`types` dict; VECTOR membership by `"VECTOR" in types[field]`; `result_set` may be `[]` (**measured, nothing indexed**) or raise/malformed (**not measured → `None`**) | engine error / malformed row must not break store setup; the two "empty" cases must not be conflated |
| 4 | `_ensure_indexes()` wiring — **three** call sites: `FalkorProjection.__init__` (`:2801`, **unguarded**), `hosted_api.py:17418`, and `hosted_api.py:25622` (inside an outer `try/except` at `:25623-25627`) | state mutation | Internal | **Unit** + Integration | attribute set iff the gate holds; WARNING at most once per (endpoint, graph) | an exception escaping `__init__`; a repeated WARNING on every `tortoise_health` probe |
| 5 | Vector-index creation (`:6346`, `:6363`) — **unchanged** | **DB write** | Out | Integration (idempotency pin) | re-running setup is harmless | a second `_ensure_indexes()` must not raise or drop the index — **pinned, not changed** |
| 6 | The process-level warned-graph latch | State / Concurrent | Internal | **Unit** | key `(endpoint, self._graph_name)` where `endpoint := self._falkordb_version_cache_key() or id(self)`, lock-guarded, process-scoped, never evicted (one small entry per endpoint+graph); reset via `_reset_vector_gap_warnings()` | a race → at worst a benign double warning; the latch suppresses the **warning only**, never the measurement |

**⚠️ Two adjacent facts that stay separate (recorded):** `self._vector_index_api` (`:2766`, consumed by `required_embedding_dim` at `:6383`) answers *"did index CREATION succeed, and by which API"*; the new `self._vector_indexed_labels` answers *"what does the engine's catalog SAY right now"*. They **can** disagree in the fail-open lane (api `'procedure'` while `db.indexes()` raised → labels `None`) and that is correct: creation-API is a write-path fact, the catalog is a read-path measurement. Neither replaces the other.

### Bug Pattern Flags

| Pattern | Signal here | Required verification |
|---|---|---|
| **Silent function skips** | the inventory read is `try/except`-guarded by design | test that a **raising** `db.indexes()` leaves `Projection.__init__` healthy **and** the attribute `None` — the guard suppresses the error, not the report's absence |
| **Conditional guards** | the real predicate is **`if _ver is None or _ver[0] >= 4:`** (`:6205`) — a **`None` version PASSES it** — plus `not _is_embedded` (`:6333`) | test **both sides of each**: `_ver = None` (read DOES run), `_ver = (3,x,y)` (does not), embedded (does not) |
| **Stale shared state** | the process-level warned-graph latch | test that a second projection on the same (endpoint, graph) does **not** re-warn but **does** re-measure its own attribute — with a fake that returns a **different** inventory on the second call |
| **N+1 queries** | one `CALL db.indexes()` per store setup | assert `sum("db.indexes()" in c for c in graph.calls) == 1` after one `_ensure_indexes()` |

### Checklist Notes

- **Contract defined?** Yes — the row shape is pinned by measurement (9 columns; `types` at index 2) and the five-reader table above records what each reader touches.
- **Empty vs null — pinned, and the two are NOT the same thing:** `result_set == []` → the set is **measured and empty** (`set()`, gate ran, nothing indexed); `result_set is None`, a raising read, or an unusable shape → the set is **`None`** = *not measured / gate skipped*. A consumer computing the gap must treat `None` as "unknown", never as "nothing indexed".
- **Malformed response:** `len(row) < 3`, non-dict `types`, or a non-list value is skipped **per row**, never aborting the loop.
- **Non-`str` input — behaviour preserved, and this is a real trap:** today `entity_type.capitalize()` raises `AttributeError`; a bare `ENTITY_TYPE_LABELS.get(entity_type)` raises `TypeError: unhashable type` for a list/dict. The implementation must guard with `isinstance(entity_type, str)` and let the non-`str` case fall through to `.capitalize()` so the **exception type is unchanged**, with a test pinning it.
- **No user-facing journey** — no `### Journey Test Map`.

### Verification Plan

> Domain: **code**. `UX=low`, `Ontology=low`, `Accessibility=low`. `Architecture=high` → unit + integration for the DB surface, plus the architectural-soundness review the mandatory `code-review` gate provides.

| Layer | Depth | What |
|---|---|---|
| **Unit** (pytest) | required | declaration ↔ query-path **provenance**; unknown/non-`str` behaviour; inventory parsing (VECTOR vs FULLTEXT, short/malformed rows, `[]` vs `None`); intersection with the served set; both gates incl. `_ver=None`; fail-open; once-per-(endpoint, graph) warning; N+1 count; concurrency |
| **Integration** (live docker, skip-if-unavailable, mirrors `tests/test_hnsw_vector_index.py`) | required | against a real store: `Point` indexed and reported; `Event`/`Object`/`Source`/`Subject` not; the report is read-only; **setup twice is harmless** (idempotency pin) |
| **CI registration** | **required** | `config/ci-surfaces.yml` must list the new test file — see Task 0 |
| **E2E / pgTAP** | skipped | no UI; no Postgres / SQL business logic |
| **Mutation proof** | required by the lane brief | Task 5 |

---

### Task 0: Register the test file in the CI manifest (do this FIRST — a new test file is not neutral)

**Intent:** A new `tests/test_*.py` absent from `config/ci-surfaces.yml` fails `manifest-integrity` **and** never runs, so the whole Integration layer this plan promises would silently not execute.
**Acceptance:** `python3 tools/ci_selection.py --integrity` exits 0 once the file exists **and** is listed; the diff to `config/ci-surfaces.yml` adds **only** the one entry for `test_4997_vector_index_parity.py` (assert with `git diff -- config/ci-surfaces.yml`); the entry carries a `#4997` comment; the edit is otherwise **additive only** (lane brief).

**Files:**
- Modify: `config/ci-surfaces.yml` (one commented entry, alphabetically placed, mirroring the `test_4999_vector_mechanism.py` entry at `:1008-1013`)
- Create: `tests/test_4997_vector_index_parity.py` (placeholder that will grow)

**Step 1** — `python3 tools/ci_selection.py --integrity` → record the baseline (expect exit 0).
**Step 2** — Create the test file with one trivial test; re-run `--integrity` → **it now reports the file missing** (this is the failure the registration prevents; record the output).
**Step 3** — Register it. ⚠️ **The tool has no per-file argument** (`tools/ci_selection.py`'s parser defines no positional, and `--register` is a bare `store_true` that sweeps **every** unlisted `tests/*.py`). So:
  - prefer a **manual, commented append** in alphabetical position (this is what produces the `#4997:` comment the entry above carries — `--register` inserts a bare line with no comment); then
  - `python3 tools/ci_selection.py --integrity` → exit 0, and `git diff -- config/ci-surfaces.yml` → exactly one added line pair.
  If `--register --surface core` is used instead (no path argument), note in the commit message that it is all-or-nothing and comment-free, and still assert the diff is one entry.
  **`core` is the measured surface:** `printf 'tortoise/security.py\n' | python3 tools/ci_selection.py --changed-files - --event pull_request` → `{"surfaces": ["core"], "full": false}` (same for `tortoise/search_engine.py`); `tortoise/projection/__init__.py` is in `SHARED_MODULES` (`tools/ci_selection.py:114`) and forces the full matrix regardless.
**Step 4** — Commit.

---

### Task 1: The declaration

**Intent:** Give the `entity_type → graph label` contract one written definition, in the module that already owns the entity-type vocabulary.
**Acceptance:** `tortoise/security.py` exposes `ENTITY_TYPE_LABELS` (exhaustive over `VALID_ENTITY_TYPES`, **and with no extra keys**) and `entity_label(et)`; `entity_label(et)` reproduces the pre-#4997 expression for every valid `et`, for an unknown-but-`str`, and **raises `AttributeError` for a non-`str` exactly as today**; `tortoise/security.py` imports nothing from `tortoise.projection` or `tortoise.search_engine`.

**Files:**
- Modify: `tortoise/security.py` (add the declaration next to `VALID_ENTITY_TYPES`, `:114-136`)
- Test: `tests/test_4997_vector_index_parity.py`

**Step 1** — Write the failing tests:

```python
from pathlib import Path

import pytest

from tortoise.security import (
    ENTITY_TYPE_LABELS, VALID_ENTITY_TYPES, entity_label,
)


def _legacy(et: str) -> str:
    """The exact pre-#4997 derivation, kept here as the parity oracle."""
    if et == "document":
        return "Source"
    return "Point" if et == "operator" else et.capitalize()


@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_declaration_is_exhaustive_and_matches_the_legacy_derivation(et):
    assert entity_label(et) == _legacy(et)


def test_declaration_has_no_extra_keys():
    # reverse direction: the declaration may not serve a label the
    # vocabulary does not admit (the D4 consistency assertion #5407 records
    # as absent today).
    assert set(ENTITY_TYPE_LABELS) == set(VALID_ENTITY_TYPES)


def test_unknown_str_keeps_legacy_behaviour():
    assert entity_label("widget") == "Widget"


@pytest.mark.parametrize("bad", [None, ["point"], {"point": 1}])
def test_non_str_raises_the_same_exception_type_as_today(bad):
    with pytest.raises(AttributeError):
        entity_label(bad)


def test_security_stays_a_stdlib_only_leaf():
    src = Path("tortoise/security.py").read_text()
    for banned in ("from .projection", "from tortoise.projection",
                   "from .search_engine", "from tortoise.search_engine"):
        assert banned not in src
```

**Step 2** — Run → **FAIL** (`ImportError: cannot import name 'ENTITY_TYPE_LABELS'`).

**Step 3** — Implement in `tortoise/security.py`. The docstring must record: (a) that the label *is* query structure, which is why it lives beside `validate_entity_type`; (b) D10's `document → :Source` and `operator → Point`; (c) that this is the contract `#5407` will extend to the other three query legs and that `#5404` will allowlist; (d) the **non-`str` trap** — implement the lookup so a non-`str` reaches `.capitalize()` and raises `AttributeError`:

```python
def entity_label(entity_type: str) -> str:
    if isinstance(entity_type, str):
        label = ENTITY_TYPE_LABELS.get(entity_type)
        if label is not None:
            return label
    # Unrecognised or non-str → the legacy derivation, verbatim: an unknown
    # str yields "<Capitalized>", a non-str raises AttributeError as it
    # always has. Falling back to .capitalize() is what keeps this a
    # *fallback* rather than a second definition.
    return entity_type.capitalize()
```

**Step 4** — Run → **PASS**. **Step 5** — Commit.

---

### Task 2: The query path reads the declaration — and the test proves PROVENANCE, not value

**Intent:** Remove the per-call derivation inside `run_vector_query` so the served set has one source, and prove the path actually *reads it* (a value-equality test cannot).
**Acceptance:** `run_vector_query` emits the label it obtained from `entity_label`; behaviour is unchanged for every valid, unknown-`str`, and non-`str` input; the test goes **RED** when the path re-derives the label even though the emitted strings are identical.

**Files:**
- Modify: `tortoise/search_engine.py` (label derivation `:932-935`; module imports — **the import MUST be `from tortoise.security import entity_label`**, i.e. a module-global binding: the provenance test patches `tortoise.search_engine.entity_label`, which only intercepts a call if it is a module global. `import tortoise.security as security` + `security.entity_label(...)` would make the sentinel test fail on CORRECT code)
- Test: `tests/test_4997_vector_index_parity.py` — including the two fixtures this task introduces

**Step 0 — the fixtures (MANDATORY, and they do not exist in the repo today).** Add them to the test file in this task, because the provenance test depends on both:

```python
import pytest


class _MockResult:
    def __init__(self, rows=None):
        self.result_set = [] if rows is None else rows


class _RecordingGraph:
    """Captures every emitted Cypher in .calls; never raises, so the
    signature-B index branch stays live (a raise would fall through to the
    brute-force path and the sentinel would never appear)."""

    def __init__(self, index_rows=None, raise_on_indexes=False):
        self.calls: list[str] = []
        self._index_rows = index_rows if index_rows is not None else []
        self._raise_on_indexes = raise_on_indexes

    def query(self, cypher, params=None, timeout=None):
        self.calls.append(cypher)
        low = cypher.lower()
        if "db.indexes()" in low:
            if self._raise_on_indexes:
                raise RuntimeError("db.indexes() unavailable")
            return _MockResult(self._index_rows)
        if "db.idx.vector.querynodes" in low:
            return _MockResult([("near-1", 0.95)])
        # MUST RAISE, mirroring tests/test_falkordb_compat.py:60-61 and the
        # measured engine (this plan's own probe table): the procedure is
        # "not registered" on this engine, which is precisely why
        # _ensure_indexes carries the `CREATE VECTOR INDEX` fallback at
        # projection/__init__.py:6357-6363. A fake that returns [] here makes
        # the procedure "succeed", sets _vector_index_api='procedure', and the
        # Cypher form is NEVER emitted - so an assertion looking for it would
        # be RED on correct code. (An earlier draft had this wrong.)
        if "createnodeindex" in low:
            raise RuntimeError(
                "Procedure `db.idx.vector.createNodeIndex` is not registered"
            )
        return _MockResult([])


@pytest.fixture
def recording_graph():
    return _RecordingGraph()


```

⚠️ **The autouse `_reset_vector_gap_warnings` fixture belongs to Task 3, not here.** It imports a Task 3 deliverable (`_reset_vector_gap_warnings`, added beside `_FALKORDB_VERSION_CACHE`) which does not exist yet — importing it in Task 2 would error every test in the file at fixture setup and contradict Task 2's own stated RED. Add it in Task 3 Step 0.

**Step 1** — Write the failing tests. **The load-bearing one monkeypatches the seam to a sentinel**, because M1 (re-deriving the label) emits the *same strings* and would pass any value-equality assertion:

```python
@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_run_vector_query_reads_the_declaration(monkeypatch, recording_graph, et):
    """PROVENANCE. A value assertion cannot see M1; this can."""
    import tortoise.search_engine as se
    monkeypatch.setattr(se, "entity_label", lambda _et: "SentinelLabel")
    run_vector_query(recording_graph, [0.1] * 384, limit=3, is_embedded=False,
                     entity_type=et, vector_index_api="cypher")
    assert "'SentinelLabel'" in " ".join(recording_graph.calls)


@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_run_vector_query_label_equals_the_declaration(recording_graph, et):
    run_vector_query(recording_graph, [0.1] * 384, limit=3, is_embedded=False,
                     entity_type=et, vector_index_api="cypher")
    assert f"'{entity_label(et)}'" in " ".join(recording_graph.calls)


def test_run_vector_query_unknown_str_uses_the_fallback(recording_graph):
    run_vector_query(recording_graph, [0.1] * 384, limit=3, is_embedded=False,
                     entity_type="widget", vector_index_api="cypher")
    assert "'Widget'" in " ".join(recording_graph.calls)
```

`vector_index_api="cypher"` (not `None`) is deliberate: it selects signature B first, matching the recorded production API (`_vector_index_api == 'cypher'`, epic #5090), so the capture exercises the live branch.

**Step 2** — Run → `test_run_vector_query_reads_the_declaration` **FAILS** (no `SentinelLabel`); the value tests pass. That is the RED this task owns.

**Step 3** — Replace the derivation with `label = entity_label(entity_type)`; add the import. Leave `id_field` untouched. **Keep `run_vector_query`'s docstring accurate** (its `entity_type` line still lists the same values).

**Step 4** — Run the full file plus `tests/test_search_engine_gaps.py tests/test_falkordb_compat.py tests/test_search_engine.py tests/test_epic903_freshness.py -v` → **PASS** (the `#172`/`#1359`/`#4028` suites are the behaviour-preservation gate). **Step 5** — Commit.

---

### Task 3: Measure and record the store's indexed label set

**Intent:** Make the indexed set a measured fact and the gap a reported one, on the store's own setup path.
**Acceptance:** `FalkorProjection._record_vector_index_inventory()` reads `CALL db.indexes()`, records the **intersection of the served labels with** the VECTOR-indexed labels as `self._vector_indexed_labels`, emits **one** WARNING per (endpoint, graph) naming the missing labels **and the open V1 decision**, is **skipped** when `_is_embedded` or when `_ver is not None and _ver[0] < 4`, and is **fail-open** — a raising or `None` read leaves store setup healthy and the attribute `None`. `result_set == []` yields `set()`, **not** `None`.

**Files:**
- Modify: `tortoise/projection/__init__.py` — the method next to `_falkordb_version_cache_key` (`:5800`); the process-level latch + `_reset_vector_gap_warnings()` beside `_FALKORDB_VERSION_CACHE` (`:53`) / `_reset_falkordb_version_cache` (`:211`); **the new import of `ENTITY_TYPE_LABELS` from `tortoise.security`** (follows the existing function-scoped `from tortoise.security import validate_rel_type` at `:5358`); the call from `_ensure_indexes` **inside** the `if not getattr(self, '_is_embedded', False):` block and **at its END** — i.e. AFTER the vector-index creation at `:6346`/`:6363`, never before it (a read placed earlier reports every served label as missing on a fresh graph, and the latch would then suppress the correct warning); `self._vector_indexed_labels = None` in `FalkorProjection.__init__` **before** `_ensure_indexes` (`:2801`)
- Test: `tests/test_4997_vector_index_parity.py`

**Step 0** — Add the autouse `_reset_vector_gap_warnings` fixture (moved here from Task 2, because it imports this task's deliverable):

```python
@pytest.fixture(autouse=True)
def _reset_vector_gap_warnings():
    from tortoise.projection import _reset_vector_gap_warnings as reset
    reset()
    yield
    reset()
```

**Step 1** — Write the failing tests:
(a) VECTOR vs FULLTEXT discrimination on a fake row set;
(b) short / non-dict-`types` / non-list-value rows skipped per row;
(c) **`result_set = []` → `set()`** and **`result_set = None` → `None`** — two separate assertions;
(d) a **raising** read → attribute `None`, **no exception out of `_ensure_indexes`** and `FalkorProjection.__init__` healthy;
(e) the gate: `_falkordb_version = None` → **read DOES run**; `(3, 2, 0)` → does not; `_is_embedded = True` → does not;
(f) `caplog`: exactly one WARNING across two successive `_ensure_indexes()` calls on the same (endpoint, graph), the message **naming V1**, and a **second call whose fake returns a DIFFERENT inventory** updates the attribute (this is what makes M4 falsifiable — with the same rows, cached and re-measured are indistinguishable);
(g) a row naming a label **outside** the served set does not enter the attribute;
(h) `sum("db.indexes()" in c for c in graph.calls) == 1` after one `_ensure_indexes()`;
(i) two threads calling the method on the same graph name → no exception, the latch holds;
(j) **write-path non-interference (falsifiable):** with a hostile/empty inventory, `_ensure_indexes` still attempts its `Point` vector-index write — assert `any(("createNodeIndex" in c) or ("CREATE VECTOR INDEX" in c.upper()) for c in graph.calls)` regardless of what the inventory read returned. The either-form assertion is deliberate: `_ensure_indexes` detects success by **absence of an exception** from `CALL db.idx.vector.createNodeIndex` and sets `_vector_index_api='procedure'` (`:6340-6352`); the `CREATE VECTOR INDEX FOR (p:Point) …` Cypher exists **only in the `except` fallback** (`:6357-6363`). With the fake raising above, the fallback IS exercised — assert on the fallback form; and confirm the property is falsifiable by checking the same assertion FAILS if the report is made to `return` before the creation block;
(k) a `_bare_projection`-shaped projection (no `.db`) → **no exception**, and the attribute is **`set()`** (its fake returns `[]` for `db.indexes()`, which means *measured and empty* — not `None`). This is the regression pin for the `AttributeError` hazard.

**Order-dependence is a real hazard here:** the latch is module-level process state and the existing `_bare_projection` helper hard-codes `_graph_name = "test_1359"` (`tests/test_falkordb_compat.py:87`). Every test above therefore uses a **graph name unique to the test**, plus an autouse fixture calling `_reset_vector_gap_warnings()` (the same shape as `tests/test_4999_vector_mechanism.py`'s `_reset_breakers`).

**Step 2** — Run → **FAIL**. **Step 3** — Implement. **The `try/except` is mandatory and load-bearing:** `_ensure_indexes` is reached **unguarded** from `FalkorProjection.__init__` (`:2801`) as well as from `hosted_api.py:17418` and `hosted_api.py:25622` (the latter inside its own outer guard), so an exception here breaks store construction. On failure (including `result_set is None`): DEBUG log, attribute `None`, `return`. On `[]`: attribute `set()`. Parse **column 2 (`types`)**; skip `len(row) < 3` and non-dict `types`. ⚠️ **The latch key must be computed INSIDE the same `try/except`.** `_falkordb_version_cache_key()` dereferences `self.db` (`conn = getattr(self.db, "connection", None)`, `:5810`), and `tests/test_falkordb_compat.py`'s `_bare_projection` (`:83-93`) builds a projection with `object.__new__` that has **no `.db`** — so an unguarded key computation raises `AttributeError` out of `_ensure_indexes` for the four existing tests at `:254,267,278,302` (which do reach the new read: `_is_embedded=False` and `_falkordb_version=(4,18,3)` pass the `:6204` gate, and their fake returns `[]` for `CALL db.indexes()`). **DECIDED (not left to the executor):** (a) is chosen — compute the key **inside** the guard, **and** harden `_falkordb_version_cache_key` with `getattr(self, "db", None)`; `_bare_projection` is left untouched. **The one key shape is `(endpoint, self._graph_name)` where `endpoint := self._falkordb_version_cache_key() or id(self)`** — the endpoint identity the version cache uses, because a graph name alone is unique per *engine*, not per process, and this plan's own reason for never caching the measurement is exactly that. The latch **suppresses the WARNING only** — the attribute is always re-measured.
**RESOLVED — both open choices are decided here, so no design decision is left to the executor:**
- **Key shape (one definition):** `endpoint := self._falkordb_version_cache_key() or id(self)`, key `= (endpoint, self._graph_name)`. The per-instance fallback (`id(self)`) is used only when the engine endpoint is unidentified, so the residual — two *unidentified-client* stores in one process sharing a latch bucket — cannot silently withhold a warning from a genuinely different store. The latch still suppresses the **WARNING only**; the attribute is always re-measured.
- **Hardening (chosen): (a)**, compute the key inside the guard, **and** change `_falkordb_version_cache_key`'s `self.db` to `getattr(self, "db", None)` (`:5807`). Verified: with `.db` absent it returns `None` (via `path = getattr(self, "_path", None)`), it does **not** raise. `_bare_projection` is therefore left untouched — those four existing tests keep exercising the real read path (attribute `set()`, since their fake returns `[]`) rather than being short-circuited.

**Step 4** — Run the file plus the suites that construct a projection and therefore execute the changed setup path: `tests/test_falkordb_compat.py tests/test_projection.py tests/test_write_ahead_mint.py tests/test_indexes.py tests/test_graphcopy_boolean_index_3154.py tests/test_divergence_conformance.py tests/test_hnsw_vector_index.py -v` → **PASS**. (The docker/`from_uri` members of that list — `test_indexes.py:507,550`, `test_graphcopy_boolean_index_3154.py`, `test_divergence_conformance.py:375`, `test_hnsw_vector_index.py` — need `TORTOISE_DB_URI`; run them under the docker lane.) **Step 5** — Commit.

---

### Task 4: Live integration + the idempotency pin

**Intent:** Prove the report against a real engine and pin the lane brief's schema-idempotency requirement (measurement shows it is **already satisfied**).
**Acceptance:** live docker (skip-if-unavailable): the store reports `Point` vector-indexed and `Event`/`Object`/`Source`/`Subject` not; the report performs no write; a **second** `_ensure_indexes()` is harmless — no exception, the `Point` VECTOR index still listed by `CALL db.indexes()` and still serving.

**Files:** `tests/test_4997_vector_index_parity.py` (docker-gated class, `TORTOISE_DB_URI`-driven, mirroring `tests/test_hnsw_vector_index.py`)

⚠️ **Use a UNIQUE, `test_`-prefixed graph** for these tests (the repo's #1647 T7 convention, e.g. `tests/test_epic903_freshness.py:536`), not a shared probe graph. `tests/test_hnsw_vector_index.py` uses a shared `torotise_hnsw` graph **where the index already exists**, so mirroring its graph choice would make "the store reports `Point` vector-indexed" pass for the wrong reason and would not distinguish a read placed before vs after creation.

**Step 1** — Write the tests; the idempotency pin asserts on `CALL db.indexes()` **before and after** the second call, not merely "no exception" — a silently dropped index is the failure that matters. **Step 2** — Run with `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'` → **expected PASS** (Task 3 already landed the report, so there is no RED here — a FAIL means a **real defect** and the lane STOPS per Step 3 rather than patching around it). **Step 3** — No production change expected; if a defect surfaces, stop and re-scope. **Step 4** — Green. **Step 5** — Commit.

---

### Task 5: Mutation proof

**Intent:** Supply the lane brief's mutation evidence — proof the new tests can fail, and that the legitimate form stays green.
**Acceptance:** five mutations applied one at a time, each **reverted** before the next (`git diff --stat` empty between them), with the observed result recorded verbatim in a draft file that Task 6 pastes into the PR body.

| # | mutation | must | detected by |
|---|---|---|---|
| **M1** | re-derive the label inside `run_vector_query` (the pre-#4997 expression) | **RED** | Task 2 *provenance* test — the sentinel never appears (**this is the mutation the value-equality test could not catch**) |
| **M2** | drop `"document"` from `ENTITY_TYPE_LABELS` | **RED** | Task 1 exhaustiveness |
| **M3** | remove the `try/except` around the inventory read | **RED** | Task 3(d) fail-open |
| **M4** | reuse the latched/cached inventory for the attribute instead of re-measuring | **RED** | Task 3(f) second call — **only because its fake returns a different inventory** |
| **M5** | record the raw engine label set instead of intersecting with the served set | **RED** | Task 3(g) |
| **L1** | the legitimate form, unmutated | **GREEN** | the whole file |

**Files:** Create `docs/plans/2026-09-25-4997-mutation-proof.md` (the record Task 6 pastes).

**Step 1** — M1 → run → capture. **Step 2** — revert; `git diff --stat` empty. **Step 3** — repeat M2–M5. **Step 4** — full file on the clean tree → GREEN. **Step 5** — write the record file; commit it **with** the change (it is the evidence).

---

### Task 6: Commit, review gate, PR

**Intent:** Land through the mandated gates and hand a reviewable PR to the merging lane.
**Acceptance:** a PR against `main` **closing #4997**, carrying the scoping-comment pointer, the mutation table, the CI-registration note, and the review attestation; **not merged**.

**Step 1** — Read `~/.pi/agent/skills/commit-workflow/SKILL.md` in full. **Step 2** — pre-flight: the targeted suites from Tasks 1–4 plus the docker-lane run under `TORTOISE_DB_URI`. **Step 3** — commit with `git commit -F` at the mandated worktree-unique path; push; open the PR with `Closes #4997` and paste the **Task 5** mutation-proof record. **Step 4** — run the `code-review` gate to convergence (fresh reviewers; re-dispatch after every fix). **Step 5** — post the review evidence; **do not merge**.

### Risk register

| risk | mitigation |
|---|---|
| the new test file never runs in CI | **Task 0** registers it and gates on `--integrity` |
| an extra round trip on the cold-start path | one `CALL db.indexes()` per `_ensure_indexes()`; the function already issues ~28 (recorded in the PR) |
| a new WARNING breaking a clean-log assertion | verified: no `caplog` count assertion targets this logger/message; no `filterwarnings`/`-W error` in `pyproject.toml`; latched to once per (endpoint, graph) |
| a latched warning hiding a genuine second-store gap | the latch key includes the **endpoint**, mirroring `_FALKORDB_VERSION_CACHE`, and suppresses only the warning — never the measurement |
| the report being dead weight (a log line nothing reads) | recorded as a known limit in the scoping artifact and put to the owner as a surface question; not silently closed |
| touching the four existing `db.indexes()` readers accidentally | the plan changes **no** existing reader; the five-reader table is documentation only |
| the latch key raising out of `_ensure_indexes` on a projection with no `.db` | the key is computed **inside** the guard, and Task 3 Step 1(k) pins the `_bare_projection` shape |
| the new test file never running | Task 0 gates on `--integrity` **and** on the diff adding exactly one entry |
| changing the setup path unnoticed by existing suites | Task 3 Step 4 runs the docker/`from_uri` suites that drive a real non-embedded `_ensure_indexes()` (`test_indexes.py`, `test_graphcopy_boolean_index_3154.py`, `test_divergence_conformance.py`, `test_hnsw_vector_index.py`), not just the fast unit set |
| a false "everything is missing" gap on a fresh graph | the read runs at the **end** of the non-embedded block, after index creation, and Task 4 uses a unique fresh graph so the assertion measures post-creation state |

### Out of scope (from the scoping artifact)

Creating any index; making index creation label-generic; moving vectors to Supabase; changing what the write path embeds; `#4999`'s `mechanism` vocabulary; the production graph; `validate_entity_type` allowlisting (#5404); migrating the other three query legs onto `ENTITY_TYPE_LABELS`, and the `migrate_kinds.ENTITY_LABELS` divergence (which lacks `operator`) (#5407); unifying the five `db.indexes()` readers (recorded in `### Pattern Research` instead).
