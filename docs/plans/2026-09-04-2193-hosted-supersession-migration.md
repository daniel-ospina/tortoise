<!-- research-path: issue #2193 scoping body (standalone — no epic doc; in-repo research: commit_ops.py helper = reference, hosted_api.py §6b = consumer, tests/test_commit_supersession_parity.py = safety net) -->

# Implementation Plan — #2193: Migrate hosted §6b supersession consumer onto shared `apply_supersessions` (Approach 1 core)

> **Status:** DRAFT — pending plan-review.
> **Tier:** Standard (project level; complexity:standard). **Team:** epistemic-team.
> **Branch base:** `origin/main` @ **16075c8f** (post-#2164: fdf53ac9 merge is an ancestor). Worktree + file line refs below are @16075c8f — the local dirty checkout predates #2164 and MUST NOT be used as the edit base (hosted_api.py local-vs-origin diff ≈ 2063 lines).
> **Approach:** **1 core ("Bare §7-parity swap")** — controller-binding decision. A2 (client-visible write-warnings) REJECTED — grows the public response surface, contradicting "zero happy-path delta," and the `warnings[]`-dict-envelope fork breaks the documented payload-determinism contract (hosted_api.py:6345-6346). A3 (entity-lane-only staged swap) REJECTED — leaves two pt_ consumers = partial consolidation (quality-over-convenience; the issue's purpose is ONE consumer discipline, #2164's drift-class root cause).

---

## 1. Problem statement

§6b (`hosted_api._execute_commit_writes`, supersession loop **hosted_api.py:6687-6741** @16075c8f) is the **last supersession consumer with zero semantic guards on its entity/Object fold lane**. It is the consumer #2164 deliberately did NOT migrate (prudent risk isolation), and it is the phase-2 half of that issue. Its inline entity lane:

- **Blind-overwrite**: folds whatever the `o.id = $ref OR o.name = $ref ... LIMIT 1` probe returns with **no terminal/conflict check** — a capture-folded A→B then a commit-resolved A→C silently rewrites `supersededBy` B→C (indicator 4 harm).
- **Self-fold**: no self-supersession guard (string, mixed id/name alias, or duplicate-name successor that aliases the target) — an Object can be folded onto itself and vanish from recall_state's default view.
- **rows[0] guess**: the blind `LIMIT 1` picks an arbitrary carrier when >1 Object claims a name (never-guess violation).
- **Dangling folds**: the successor is never probed — a fold to a name that is no Object (or not visible to recall) still fires.
- **Terminal re-clobber**: an already-terminal Object is re-folded unconditionally on every re-record.
- **C2 journaling gap**: `_emit_event("ObjectSuperseded", {payload dict}, id=obj_id)` — positional payload + id kwarg — journals `{type, id}` only on the JSONL line (payload goes to the GraphEvent store). Replay fold restores status but wipes `supersededBy` to `""` (fold SET is unconditional); `session_id` is lost from the GraphEvent payload. Legacy id-less folds journal **nowhere** (id=None → JSONL early-return; M2 provenance gap).
- **Positional emit loses `session_id`** from the GraphEvent payload (payload = `{id, name, supersedes_by, evidence}` only).

The shared helper `tortoise.commit_ops.apply_supersessions` (used by capture `sdk._extract_session_v2` @sdk.py:2620-2621 and eval `tools/longmem_eval/ingest_v2.py`) carries the full guard set: terminal keep-first dedup, self-supersession skip (incl. id/name-alias and duplicate-name self-alias scans), >1-name never-guess, dangling/not-visible-successor skip, legacy id-less canonical-id synthesis, id-style journaled emit with `session_id`, count-verified fold. **§6b must be migrated onto it so hosted inherits every guard with zero happy-path delta.**

Indicator-1 citation note: the issue body's "~:6140-6185" is pre-#2164 line drift; the live consumer is **6687-6741** @16075c8f. The plan and its stale-ref sweep use the live numbers.

## 2. Recommended solution — Approach 1 core (binding)

Replace the **whole §6b loop (both the pt_ lane AND the entity lane — hosted_api.py:6687-6741)** with **one** `apply_supersessions(proj, sdk, payload.supersessions, session_id=session_id, warn=_logger.warning)` call, mirroring the §7 precedent directly below it (lazy `from tortoise.commit_ops import ...` at hosted_api.py:6761-6762, direct call). No A3 partial swap, no A2 response-surface growth.

Contract decisions (all binding):

1. **warn passed EXPLICITLY** as `hosted_api._logger.warning` (`_logger = logging.getLogger(__name__)` at hosted_api.py:79) — NOT the helper default (`commit_ops`' own logger). Attribution stays on `tortoise.hosted_api`; the capture-vs-commit client-visibility asymmetry (capture surfaces supersession warnings in its HTTP/meta response; hosted commit does not — hosted's `warnings[]` is L1/L2-domain warnings only, payload-deterministic per hosted_api.py:6345-6346) is a **pre-existing structural asymmetry, documented as a known limitation, out of scope**. Logger-only warn is the chosen hosted contract (problem-verify authorized).
2. **Typed records pass through unchanged**: `payload.supersessions` = list of `commit_schema.SupersessionRecord` models; the helper normalizes via `_sr_attr` (dict-or-model) — no conversion needed.
3. **No outer try/except added** — §6b's raw graph probes propagate DB errors into the handler's fail-closed guard today; the helper raises nothing per-record (warns) and the call site sits in the same guard (mirrors §7's unprotected direct call). Capture's defensive wrapper exists because capture is not inside that guard — hosted is.
4. **§7 untouched** (its own lazy import stays; zero delta to the operator block).
5. pt_ lane semantics are preserved by the helper: `pt_<sha>` → `sdk.supersede` CORRECTS; **terminal pt_ olds skip SILENTLY** (idempotent — E5's terminal-skip test stays green); missing pt_ ref → warn fail-open.
6. D6 probe-shape decision (indicator 3): §6b's per-record single-row `LIMIT 1` probes are **deleted with the loop**; the helper's batched `IN` probes + successor-first visibility scan become the **one documented choice** (documented in `commit_ops.apply_supersessions`' docstring — no code change needed there beyond the phase-2 deferral wording, see Task 5). No silent query-count change anywhere: hosted now runs exactly the probe shape capture/eval run.

## 3. Implementation plan (TDD-ordered — RED-first per behavioral task)

> All steps run in the worktree (Task 1). Docker lane (AGENTS.md epic #1647): pytest defaults to docker — export `TORTOISE_DB_URI` before every run. Commits are green-only (repo commit gate); RED observations are local run results, not committed states.

### Task 1: Worktree + baseline verification

**Intent:** Establish an edit base off 16075c8f (post-#2164) with a green starting point so the RED-first flips are attributable to the migration alone.
**Acceptance:** Fresh worktree on `feat/2193-hosted-supersession-migration` @16075c8f; parity + E5 suites green at base.
**Files:** none modified.

**Step 1.1 — Create the worktree (origin/main @ 16075c8f):**

```bash
cd /Users/danielospina/Documents/GitHub/tortoise
git worktree add -b feat/2193-hosted-supersession-migration ../tortoise-wt-2193 16075c8f
cd ../tortoise-wt-2193
uv sync   # min uv 0.6.0
```

**Step 1.2 — Verify base + green:**

```bash
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_commit_supersession_parity.py tests/test_commit_endpoint.py -v
```

Expected: PASS — both parity tests (`test_parity_helper_vs_hosted_section6b_identical_end_state`, `test_hosted_section6b_arm_alone_produces_the_fold`) + all of `TestE5PointSupersessions` (3 tests) and the rest of the endpoint file. (FalkorDB must be up: `docker compose -f ../eldato/operations/memory/docker-compose.yml up -d`.)

**Step 1.3 — Confirm the edit anchors @16075c8f:**

```bash
git show origin/main:tortoise/hosted_api.py | sed -n '6687,6741p'   # §6b loop
git show origin/main:tortoise/hosted_api.py | sed -n '6752,6766p'   # §7 precedent
```

Expected: the §6b comment block through the final `_logger.warning(...)` of the loop, then §7's `from tortoise.commit_ops import apply_payload_operators` + call.

### Task 2 (RED): Endpoint negative suite for the entity fold lane

**Intent:** Document §6b's CURRENT blind behavior as failing endpoint tests (the guard set that must land), mirroring `TestE5PointSupersessions` (test_commit_endpoint.py:780-887) — `client` fixture + `_team_sdk()` + `_commit`/`_raw_payload` helpers, POST `/v1/sessions/commit`.
**Acceptance:** New `Test6bEntitySupersessionGuards` — happy-path (a) green at base; guards (b)-(g) FAIL at base with assertions that capture the blind-fold behavior; all seven flip green after Task 4.
**Files:**
- Modify: `tests/test_commit_endpoint.py` (add `import json`; add suite after `TestE5PointSupersessions`, i.e. after line 886)

**Step 2.1 — Add `import json`** next to the existing stdlib imports (`import os` / `import tempfile` region):

```python
import json
```

**Step 2.2 — Add the module-level seed/supersede/read helpers** (after `TestE5PointSupersessions`): prior-commit seeding (conftest direct-write convention — a fresh temp DB per `client` fixture isolates every test):

```python
def _seed_objects(client, session_id: str, names: list[str]) -> None:
    """Prior-commit seeding: LIVE Objects by name (canonical ids minted by
    create_entity in step 6) + one net-new point, so a later commit can
    supersede the objects (#2193 fixture — mirrors E5's seed-a-prior-state
    convention)."""
    content = f"seed-{session_id}-{'-'.join(names)}"
    raw = _raw_payload(1, session_id=session_id, points=[
        _point(0, id=point_content_id(content), content=content,
               about_entities=[]),
    ], entities=[
        {"name": n, "kind": "Project", "passes_frequency_gate": True}
        for n in names
    ])
    r = _commit(client, raw)
    assert r.status_code == 200, r.text


def _supersede_commit(client, session_id: str, ref: str, sby: str, *,
                      evidence: str = "entity lifecycle supersedes",
                      entities=()) -> None:
    """A commit whose ONLY supersession work is one entity record ref→sby.
    Successor entities ride payload.entities (the extractor contract — step 6
    pre-writes net-new entities before §6b); refs may be id OR name. One
    net-new point keeps the commit off the L1-replay/held paths."""
    content = f"resolve-{session_id}-{ref}-{sby}"
    raw = _raw_payload(1, session_id=session_id, points=[
        _point(0, id=point_content_id(content), content=content,
               about_entities=[]),
    ], entities=[
        {"name": n, "kind": "Project", "passes_frequency_gate": True}
        for n in entities
    ], supersessions=[
        {"superseded": ref, "supersedes_by": sby, "evidence": evidence},
    ])
    r = _commit(client, raw)
    assert r.status_code == 200, r.text


def _object_row(name: str):
    g = _team_sdk()._get_proj().g
    rows = g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id, o.name, o.status, o.supersededBy",
        params={"n": name}).result_set
    return rows[0] if rows else None
```

**Step 2.3 — Add the suite** (tests (a)-(g); each asserts the GUARDED outcome, i.e. what the helper must do):

```python
class Test6bEntitySupersessionGuards:
    """#2193 — hosted §6b (hosted_api._execute_commit_writes) migrated onto
    the shared commit_ops.apply_supersessions: the entity/Object fold lane
    inherits the full guard set (keep-first terminal dedup, self-supersession
    skip, >1-name never-guess, dangling-successor skip, id-style journaled
    emit with session_id). Written RED against §6b's CURRENT blind behavior
    (fold-without-checks) and flipped green by the migration."""

    def test_happy_path_entity_fold(self, client):
        """(a) happy path — live Object A folded by successor B (both from a
        prior commit): status superseded, supersededBy=B, B stays live."""
        _seed_objects(client, "sE1", ["approach-A", "approach-B"])
        _supersede_commit(client, "sE2", "approach-A", "approach-B",
                          entities=["approach-B"])
        row = _object_row("approach-A")
        assert row and row[2] == "superseded" and row[3] == "approach-B"
        assert _object_row("approach-B")[2] == "live"

    def test_divergent_successor_keep_first(self, client):
        """(b) A→B folded, then A→C re-recorded: keep-first — supersededBy
        STAYS B (the fold never blind-overwrites). RED pre-migration: §6b
        silently re-folds A→C."""
        _seed_objects(client, "sK1", ["approach-A", "approach-B"])
        _supersede_commit(client, "sK2", "approach-A", "approach-B",
                          entities=["approach-B"])
        _supersede_commit(client, "sK3", "approach-A", "approach-C",
                          entities=["approach-C"])
        row = _object_row("approach-A")
        assert row and row[2] == "superseded" and row[3] == "approach-B"
        assert _object_row("approach-C")[2] == "live"

    def test_same_successor_rededup_single_journal(self, client):
        """(c) A→B folded, then the SAME claim A→B re-recorded (new ccid —
        content differs): silent terminal dedup — exactly ONE ObjectSuperseded
        journal line. RED pre-migration: §6b emits + re-folds every record."""
        _seed_objects(client, "sD1", ["approach-A", "approach-B"])
        _supersede_commit(client, "sD2", "approach-A", "approach-B",
                          entities=["approach-B"])
        _supersede_commit(client, "sD3", "approach-A", "approach-B",
                          entities=["approach-B"])
        g = _team_sdk()._get_proj().g
        n = g.query(
            "MATCH (e:GraphEvent {type:'ObjectSuperseded'}) RETURN count(e)",
        ).result_set[0][0]
        assert n == 1, "same-successor re-record must dedup (no second journal)"

    def test_duplicate_name_never_guess(self, client):
        """(d) >1 Object claims the same name (legacy/raw-created second
        carrier bypassing create_entity's MERGE-by-name): the ref probe must
        NOT pick one — NEITHER folds. RED pre-migration: §6b LIMIT 1 folds
        rows[0] arbitrarily."""
        sdk = _team_sdk()
        _seed_objects(client, "sN1", ["dup-object"])
        sdk._get_proj().g.query(
            "CREATE (o:Object {id:'obj-legacy-dup', name:'dup-object', "
            "status:'live'})")
        _supersede_commit(client, "sN2", "dup-object", "other-new",
                          entities=["other-new"])
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {name:'dup-object'}) RETURN o.status",
        ).result_set
        assert len(rows) == 2
        assert all(s == "live" for (s,) in rows), \
            "ambiguous >1-name ref must fold NEITHER (never-guess)"

    def test_self_supersession_id_name_alias_stays_live(self, client):
        """(e) mixed id/name self-alias — superseded = the canonical id,
        supersedes_by = that same Object's name: skipped, the Object STAYS
        LIVE. RED pre-migration: §6b folds the Object onto itself (terminal,
        invisible to recall's default view)."""
        _seed_objects(client, "sS1", ["solo-object"])
        obj_id = _object_row("solo-object")[0]
        assert obj_id, "create_entity must mint a canonical id"
        _supersede_commit(client, "sS2", obj_id, "solo-object")
        row = _object_row("solo-object")
        assert row and row[2] == "live", \
            "self-supersession (id/name alias) must be skipped"

    def test_dangling_successor_skipped(self, client):
        """(f) successor is no Object (never written, not in payload
        entities): the fold must NOT fire — ref Object stays live. RED
        pre-migration: §6b folds to the dangling display string."""
        _seed_objects(client, "sF1", ["lone-object"])
        _supersede_commit(client, "sF2", "lone-object", "ghost-successor")
        row = _object_row("lone-object")
        assert row and row[2] == "live", \
            "dangling successor must not fold the ref Object"

    def test_entity_fold_journals_session_id_in_graph_event(self, client):
        """(g) C2/journal delta — the ObjectSuperseded GraphEvent payload is
        id-style AND carries the committing session_id. RED pre-migration:
        §6b's positional emit produces {id,name,supersedes_by,evidence} —
        no session_id."""
        _seed_objects(client, "sG1", ["approach-A", "approach-B"])
        _supersede_commit(client, "sG2", "approach-A", "approach-B",
                          entities=["approach-B"])
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (e:GraphEvent {type:'ObjectSuperseded'}) RETURN e.payload",
        ).result_set
        assert len(rows) == 1
        payload = json.loads(rows[0][0])
        assert set(payload) == {"id", "name", "supersedes_by",
                                "session_id", "evidence"}, payload
        assert payload["session_id"] == "sG2"
        assert payload["id"] == _object_row("approach-A")[0]
        assert payload["name"] == "approach-A"
        assert payload["supersedes_by"] == "approach-B"
```

**Step 2.4 — Run the suite at base; verify RED (documents current blind behavior):**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_commit_endpoint.py::Test6bEntitySupersessionGuards -v
```

Expected: **(a) PASS** (happy path identical pre/post — the zero-delta anchor); **(b) FAIL** (`supersededBy` clobbered to `approach-C`), **(c) FAIL** (2 journal lines), **(d) FAIL** (one duplicate folded), **(e) FAIL** (self-folded terminal), **(f) FAIL** (folded to `ghost-successor`), **(g) FAIL** (`KeyError: 'session_id'`). These six failures ARE §6b's blind behavior, pinned as tests.

**Step 2.5 — No commit** (red state). Proceed to Task 3.

### Task 3 (RED): Wiring-spy test in the parity file

**Intent:** Pin the migration's shape at the seam — §6b must become EXACTLY ONE `apply_supersessions` call with the payload's typed records, `session_id`, and `hosted_api._logger.warning`. RED pre-migration (the helper is never called).
**Acceptance:** New spy test fails at base (zero calls) and passes after Task 4.
**Files:**
- Modify: `tests/test_commit_supersession_parity.py` (append after line 281)

**Step 3.1 — Append the spy test** (reuses the existing `_seed_baseline`/`_write_successors`/`_commit_payload_and_plan` harness):

```python
def test_hosted_commit_wires_apply_supersessions_once(monkeypatch, tmp_path):
    """#2193 wiring spy — _execute_commit_writes §6b is EXACTLY ONE
    apply_supersessions call with the payload's typed SupersessionRecords,
    session_id, and the hosted module logger as warn. No inline pt_/entity
    consumer may remain. The monkeypatch lands on the module attribute; the
    function's lazy `from tortoise.commit_ops import apply_supersessions`
    binds at call time, so the spy is what §6b invokes."""
    old_pt_id = _pt_id(OLD_PT_CONTENT)
    new_pt_id = _pt_id(NEW_PT_CONTENT)
    sdk = TortoiseSDK(str(tmp_path / "wiring.db"))
    _seed_baseline(sdk, old_pt_id)
    _write_successors(sdk, new_pt_id)
    payload, plan = _commit_payload_and_plan(old_pt_id, new_pt_id)

    calls: list = []

    def _spy(proj, sdk_, records, **kwargs):
        calls.append((proj, sdk_, list(records), kwargs))

    monkeypatch.setattr("tortoise.commit_ops.apply_supersessions", _spy)
    hosted_api._execute_commit_writes(sdk, payload, plan)

    assert len(calls) == 1, \
        f"§6b must be one apply_supersessions call, got {len(calls)}"
    proj, sdk_, records, kwargs = calls[0]
    assert proj is sdk._get_proj() and sdk_ is sdk
    # typed records, no dict conversion (helper normalizes via _sr_attr)
    assert records == list(payload.supersessions)
    assert kwargs["session_id"] == SESSION_ID
    assert kwargs["warn"] is hosted_api._logger.warning
```

**Step 3.2 — Run; verify RED:**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_commit_supersession_parity.py::test_hosted_commit_wires_apply_supersessions_once -v
```

Expected: FAIL — `len(calls) == 0` (the inline §6b loop never calls the helper).

**Step 3.3 — No commit.** Proceed to Task 4.

### Task 4 (GREEN): Migrate §6b → one `apply_supersessions` call; repurpose the parity file

**Intent:** The whole-loop swap (Approach 1 core — NOT the entity-only A3 slice) + parity-file repurpose (differential now vacuous: both arms ARE the helper).
**Acceptance:** §6b loop gone; endpoint guards (b)-(g) + parity spy flip green; `TestE5PointSupersessions` still green; parity file repurposed (differential deleted, arm-alone retained as hosted-path wiring smoke, module docstring rewritten); helper byte-untouched.
**Files:**
- Modify: `tortoise/hosted_api.py:6687-6741` (§6b comment + loop → one call)
- Modify: `tests/test_commit_supersession_parity.py` (delete differential test 208-261; rename arm-alone test 264; repurpose module docstring 1-30)

**Step 4.1 — Replace the whole §6b block (hosted_api.py:6687-6741).** The comment block + both lanes (`for sr in payload.supersessions:` … final `_logger.warning("ObjectSuperseded emit failed ...")`) become:

```python
    # ── 6b. Supersessions — client-derived records (the deterministic channel
    # for the Object status fold, #1350), applied via the SHARED
    # apply_supersessions helper (#2164/#2193): pt_<sha> refs → the canonical
    # supersede() CORRECTS (terminal-probed, idempotent); entity records →
    # id-style ObjectSuperseded journal (full provenance incl. session_id) +
    # count-verified _fold_object_superseded. ONE consumer-side discipline
    # with capture (_extract_session_v2) and eval ingest_v2 — §6b's inline
    # consumer was the last divergent copy. Guards inherited: terminal
    # keep-first (the fold never blind-overwrites), self-supersession skip
    # (string + id/name alias), >1-name never-guess, dangling / not-visible
    # successor skip, legacy id-less canonical-id synthesis. Every skip and
    # failure surfaces through _logger.warning (hosted attribution) — never a
    # silent drop; per-record fail-open — a supersession write never fails
    # the commit (warn-only, matches the extractor's never-guess discipline).
    # The step-6 entity writes above have landed the payload's net-new
    # successors, so successor probes resolve. ──
    from tortoise.commit_ops import apply_supersessions
    apply_supersessions(
        proj, sdk, payload.supersessions,
        session_id=session_id, warn=_logger.warning,
    )
```

(`proj` = `sdk._get_proj()` and `session_id` are already in scope at 6478/6481. §7's own lazy import at 6761 stays untouched.)

**Step 4.2 — Run the endpoint suite; verify the flip:**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_commit_endpoint.py::Test6bEntitySupersessionGuards \
                   tests/test_commit_endpoint.py::TestE5PointSupersessions -v
```

Expected: ALL PASS — (b)(c)(d)(e)(f)(g) flipped green (guards now apply through the endpoint); (a) still green (zero happy-path delta); E5's three pt_ tests still green (pt_ lane semantics preserved — terminal silent skip, missing-ref warn fail-open).

**Step 4.3 — Run the parity file; verify the spy flip:**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_commit_supersession_parity.py -v
```

Expected: spy PASS; differential + arm-alone still PASS (differential is now vacuous — both arms are the helper — which is why it is deleted next).

**Step 4.4 — Repurpose the parity file.** Its purpose was pinning a two-implementation interim safe; the interim is over.

4.4a — **Delete the differential** `test_parity_helper_vs_hosted_section6b_identical_end_state` (lines 208-261): helper-vs-§6b parity is vacuous when §6b no longer exists as an implementation.
4.4b — **Rename the arm-alone test** (line 264) to its post-migration role — a hosted-path wiring/end-state smoke — and extend its docstring:

```python
def test_hosted_commit_writes_supersession_end_state(tmp_path):
    """#2193 wiring smoke (ex-#2164 arm-alone non-vacuity): the hosted commit
    path ALONE must produce the fold — old pt terminal with a single CORRECTS
    edge from the successor, old Object superseded by the successor name.
    Post-migration the hosted path IS apply_supersessions; this test pins the
    end-state contract (_EXPECTED_END_STATE) through the real endpoint write
    phase, so a dropped/mis-resolved record fails here, not just in the
    differential. If §6b's records were dropped or mis-resolved, this test
    fails."""
```

4.4c — **Rewrite the module docstring** (lines 1-30) to the post-migration purpose (drop the two-implementation/differential framing and the stale `~:6140-6185` / `hosted_api.py:6140` citations):

```python
"""#2193 — hosted-path supersession wiring guards (ex-#2164 Task 9a drift guard).

WHY THIS FILE EXISTS: #2164's root cause was THREE divergent consumers of the
supersession discipline (capture, eval ingest, the hosted commit endpoint).
``tortoise.commit_ops.apply_supersessions`` is now the ONE consumer-side
discipline — capture (``sdk._extract_session_v2``), eval ingest
(``tools/longmem_eval/ingest_v2.py``), and the hosted commit endpoint
(``hosted_api._execute_commit_writes`` §6b, migrated in #2193) all call it.
The original differential parity test (helper arm vs hosted §6b inline arm,
#2164 Task 9a) is DELETED — with §6b migrated, both arms ARE the helper and
the comparison is vacuous. What remains pins the hosted WRITING of the helper:

- test_hosted_commit_wires_apply_supersessions_once — the wiring spy: §6b is
  exactly ONE apply_supersessions call with the payload's typed records,
  session_id, and hosted_api._logger.warning as warn (no inline consumer).
- test_hosted_commit_writes_supersession_end_state — the end-state contract
  through the real write phase (old pt superseded + single CORRECTS edge;
  old Object superseded by the successor name; successor live).

The end-state contract both consumers converged on (the #2164 parity
contract) is asserted by the smoke test above via _EXPECTED_END_STATE.

Test env: docker lane (TORTOISE_DB_URI set — see AGENTS.md). Each SDK
constructs with its own db_path; under the test-session redirect
(tortoise/projection/__init__.py, epic #1647) distinct paths land on distinct
derived server graphs — tests are isolated.
"""
```

**Step 4.5 — Run the parity file; verify green:**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_commit_supersession_parity.py -v
```

Expected: PASS — spy + renamed smoke (2 tests).

**Step 4.6 — Commit** (the behavioral change + its RED-first tests, green-only):

```bash
git add tortoise/hosted_api.py tests/test_commit_endpoint.py tests/test_commit_supersession_parity.py
git commit -m "feat(commit): migrate hosted §6b supersession consumer onto shared apply_supersessions (#2193)

Whole §6b loop (pt_ + entity lanes) → one apply_supersessions call
(session_id, warn=_logger.warning). Hosted inherits the #2164 guard set:
terminal keep-first, self-supersession skip, >1-name never-guess,
dangling-successor skip, id-style journaled emit with session_id (C2 gap
closed). Zero happy-path delta. Parity file repurposed: differential deleted
(both arms are the helper), arm-alone retained as hosted-path end-state
smoke, wiring spy added. Guard negatives RED-first at tests/test_commit_endpoint.py::Test6bEntitySupersessionGuards."
```

### Task 5: Stale-ref sweep (docs + comments)

**Intent:** Remove every "(phase-2) hosted §6b"-deferral / divergence reference left behind by the migration — the codebase must not describe a consumer that no longer exists.
**Acceptance:** commit_ops.py + docs/event-catalog.md carry no §6b-as-separate-consumer wording; grep sweep for `§6b|6140|phase-2.*6b` finds only the new hosted_api comment.
**Files:**
- Modify: `tortoise/commit_ops.py` (comment 139-143; docstring 145-154; never-guess comment 278-279)
- Modify: `docs/event-catalog.md` (line 18)

**Step 5.1 — commit_ops.py — drop the phase-2 deferral** in the block comment above `apply_supersessions` (139-143):

```python
# OR name; pt_<sha> refs are point content-addressed ids, dispatched by
# prefix) OR commit_schema.SupersessionRecord models (the commit reconcile
# records). Extracted here so capture (_extract_session_v2), eval ingest_v2,
# and (phase-2) hosted §6b share ONE consumer-side discipline.
```

becomes:

```python
# OR name; pt_<sha> refs are point content-addressed ids, dispatched by
# prefix) OR commit_schema.SupersessionRecord models (the commit reconcile
# records). Extracted here so capture (_extract_session_v2), eval ingest_v2,
# and the hosted commit endpoint (_execute_commit_writes §6b, migrated in
# #2193) share ONE consumer-side discipline.
```

**Step 5.2 — commit_ops.py — docstring deferral (145-154):**

```python
    #2164: shared by capture (_extract_session_v2), eval ingest_v2,
    and (phase-2) hosted §6b. warn() receives every skip/failure —
```

becomes:

```python
    #2164/#2193: shared by capture (_extract_session_v2), eval ingest_v2,
    and the hosted commit endpoint (_execute_commit_writes §6b). warn()
    receives every skip/failure —
```

**Step 5.3 — commit_ops.py — the never-guess "deliberate divergence" comment (278-279)** — no divergence exists once §6b is gone; keep the discipline, drop the contrast:

```python
                # never-guess — deliberate divergence from hosted §6b's blind
                # LIMIT 1: two Objects claim the same name, do not pick one.
```

becomes:

```python
                # never-guess: two Objects claim the same name, do not pick
                # one (a blind LIMIT 1 would fold an arbitrary carrier).
```

**Step 5.4 — docs/event-catalog.md line 18 — the ObjectSuperseded row** now describes ONE emit path:

```markdown
| `ObjectSuperseded` | 1 | hosted commit endpoint (`hosted_api._execute_commit_writes` §6b) + capture (`sdk._extract_session_v2`) + eval (`tools/longmem_eval/ingest_v2`) — entity-level supersession records; ALL THREE emit via the shared `commit_ops.apply_supersessions` (id-style kwargs — #1350/#2164/#2193) | `id`, `name`, `supersedes_by`, `session_id`, `evidence` (id-style kwargs — every field rides the JSONL line AND the synthesized GraphEvent payload) | Hosted commit endpoint / SDK capture / eval ingest (all via `commit_ops.apply_supersessions`) |
```

**Step 5.5 — Grep sweep** — no stale deferral/divergence references remain:

```bash
git grep -n "phase-2) hosted\|deliberate divergence from hosted\|journals id-only and omits session_id\|6140-6185"
```

Expected: no output.

**Step 5.6 — Run the affected suites (doc/comment edits are non-behavioral; guard the helper's consumers):**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_commit_supersession_parity.py tests/test_commit_endpoint.py -v
```

Expected: PASS.

**Step 5.7 — Commit:**

```bash
git add tortoise/commit_ops.py docs/event-catalog.md
git commit -m "docs(commit): drop §6b phase-2 deferral + divergence references post-migration (#2193)"
```

### Task 6: Full verification, delta statement, PR

**Intent:** Prove zero regression across every supersession consumer surface and ship via the repo commit gate.
**Acceptance:** Full target + related suites green; helper byte-unchanged; PR opened via commit-workflow.
**Files:** none (verification + PR).

**Step 6.1 — Full target suites + related consumer suites:**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest tests/test_commit_endpoint.py tests/test_commit_supersession_parity.py \
                   tests/test_capture_session.py tests/test_capture_session_supersession_e2e.py \
                   tests/test_lme_ingest_v2_supersession.py tests/test_status_projection.py -v
```

Expected: PASS. (The capture/eval/projection files exercise the UNCHANGED helper — they guard the other consumers against any collateral from the hosted swap.)

**Step 6.2 — Verify zero helper edits:**

```bash
git diff 16075c8f --stat -- tortoise/commit_ops.py   # expected: comment/docstring lines only (Task 5)
git diff 16075c8f -- tortoise/commit_ops.py | grep '^[-+]' | grep -v '^[-+][-+]' | head
```

Expected: only the 139-143 / 145-154 / 278-279 comment-and-docstring rewrites — no logic delta.

**Step 6.3 — Write the delta statement into the PR description** (see §7 of this plan for the canonical text).

**Step 6.4 — Open the PR through the repo gate:**

```bash
# invoke the commit-workflow skill from the worktree (pre-flight + PR + code-review gate)
```

**Step 6.5 — Apply the `planned` label per pipeline** (post plan-review): `gh issue edit 2193 --add-label planned` (remove `scoping`/`planning` as the pipeline advances).

## 4. Testing strategy

**RED-first discipline (per behavioral task):** Task 2 writes the endpoint guard negatives that FAIL against §6b's current blind behavior (b/c/d/e/f/g) plus the happy-path anchor (a); Task 3 writes the wiring spy that FAILS pre-migration (helper never called); Task 4's swap flips all of them green. The failing runs are local proof-of-RED (the repo commit gate is green-only — tests and implementation land in the same Task-4 commit, with the RED evidence documented in the commit message).

### Pattern Research

> **Findings date:** 2026-09-04
> Gate skipped: plan touches zero third-party dependencies — pure in-repo refactor onto an existing, tested helper (issue #2193 body: "Research: none needed — in-repo: the helper is the reference (commit_ops.py), §6b is the consumer to migrate, the parity test is the safety net"). Prior research intake: full issue-scoping body (O/I/T, context, known-gap list, verification checklist, complexity ratings) consumed above; codebase exploration @16075c8f verified every anchor cited in this plan (line numbers, helper semantics, _emit_event payload synthesis, GraphEvent node schema, endpoint test harness, parity file structure).

### Integration Surface Map (test-design)

| Surface | Boundary | Test layer | Where |
|---|---|---|---|
| §6b loop → helper call | in-process seam (`_execute_commit_writes`) | integration (direct drive, docker lane) | wiring spy + end-state smoke, `tests/test_commit_supersession_parity.py` |
| POST `/v1/sessions/commit` entity supersession end-state | HTTP + team graph | integration (endpoint, `client` fixture) | `Test6bEntitySupersessionGuards` (a)-(f), `tests/test_commit_endpoint.py` |
| ObjectSuperseded journal (GraphEvent payload) | `event_store` append | integration assert (endpoint read) | guard test (g) — payload `json.loads` + id-style keys + `session_id` |
| pt_ lane (supersede CORRECTS) | in-process + graph | integration (regression gate) | `TestE5PointSupersessions` stays green (unchanged) |
| Helper guard semantics | pure consumer logic | (already covered by #2164 helper tests) | regression only — helper byte-untouched (Task 6.2) |
| Other consumers (capture/eval/projection) | shared helper | integration regression | `test_capture_session.py`, `test_capture_session_supersession_e2e.py`, `test_lme_ingest_v2_supersession.py`, `test_status_projection.py` |
| Docs/comments (event-catalog, commit_ops) | n/a | diff + grep sweep | Task 5 |

Bug-pattern flags: blind-overwrite (keep-first — (b)); ambiguity rows[0]-guess (>1-name never-guess — (d)); self-reference alias (— (e)); dangling successor (— (f)); silent-journal drop / missing provenance (id-style + session_id — (g)); two-implementation drift recurrence (wiring spy + smoke). Checklist notes: every negative asserted through the ENDPOINT (the §6b seam) — never through a mocked helper; fixture seeding follows the file's prior-commit/direct-write convention; fresh temp DB per `client` fixture isolates counts (test (c)'s journal-count assert is per-test deterministic).

### Journey Test Map

Skipped — no user-facing journeys. The commit endpoint's consumers are extraction clients over HTTP; outcome-level coverage = the endpoint suite above (a supersession record in → the guarded Object/Point end-state + journal out).

## 5. Verification plan (per issue indicators + problem-verify requirements)

Docker lane, from the worktree, all commands in Task 6.1 plus:

| # | Indicator / requirement | Verification |
|---|---|---|
| I1 | §6b loop (6687-6741) replaced by one `apply_supersessions` call, `warn=_logger.warning`-compatible, dict-or-model normalization | Wiring spy asserts EXACTLY ONE call with typed records + `session_id` + `warn is hosted_api._logger.warning` (Task 3/4); code review of the swap |
| I2 | C2 gap closed — id-style emit (id/name/supersedes_by/session_id/evidence as kwargs) | Endpoint guard (g): GraphEvent payload carries `session_id` + full id-style keyset; event-catalog.md:18 row updated |
| I3 | D6 probe-shape — one documented choice, no silent query-count change | Whole-loop swap deletes §6b's per-record `LIMIT 1` probes; the helper's batched probes are the one documented choice (its docstring); no probe code remains in hosted |
| I4 | Divergent-successor blind overwrite fixed (keep-first) | Endpoint guard (b): A→B then A→C → `supersededBy` stays B; guard (c): same-successor re-record dedups to a single journal |
| I5 | Parity drift guard green, extended/converted post-migration | Differential deleted (vacuous — both arms ARE the helper); arm-alone retained as hosted-path end-state smoke; spy added; file green (Task 4.5) |
| I6 | Existing hosted commit tests green | `TestE5PointSupersessions` + full `test_commit_endpoint.py` green (Task 4.2/6.1) |
| REQ 1 | Whole §6b loop (pt_ + entity) → one helper call; §7 lazy-import precedent; `session_id` in scope | Task 4.1 diff; §7 (6761-6762) untouched |
| REQ 2 | `warn=_logger.warning` explicit (hosted_api.py:79), not helper default | Wiring spy `kwargs["warn"] is hosted_api._logger.warning` |
| REQ 3 | Endpoint negatives (a)-(g) via POST `/v1/sessions/commit` | `Test6bEntitySupersessionGuards` — mirrors TestE5 harness (client + `_team_sdk` + `_commit`) |
| REQ 4 | Parity repurpose: delete differential; keep/rename arm-alone; ADD wiring spy | Task 4.4 |
| REQ 5 | Stale refs: commit_ops "phase-2 hosted §6b" (139-143, 145-154), 278-281 divergence comment, event-catalog.md:18, parity docstring citations | Task 5 + grep sweep (5.5) |
| REQ 6 | TestE5PointSupersessions stays green (pt_ regression gate) | Task 4.2 |
| REQ 7 | Implement off origin/main @16075c8f in a fresh worktree | Task 1.1 |
| REQ 8 | Delta statement | §7 of this plan; PR description (Task 6.3) |

**Verification routing (test-routing):** domain = code; complexity architecture=standard, ontology=low; UX/accessibility = not applicable (no UI — ux-verification skipped). Layers: integration (docker lane) only — unit layer unnecessary (helper unchanged; guard semantics exercised through the hosted seam integrationally). Deferred: none.

## 6. Acceptance criteria

1. `hosted_api.py:6687-6741` contains no inline supersession loop — pt_ and entity lanes are gone; the §6b region is the §7-mirror lazy import + one `apply_supersessions(proj, sdk, payload.supersessions, session_id=session_id, warn=_logger.warning)` call (I1, REQ 1-2).
2. `Test6bEntitySupersessionGuards` (a)-(g) all green through `POST /v1/sessions/commit`, with (b)/(c)/(d)/(e)/(f)/(g) demonstrated RED at base (I4, REQ 3).
3. GraphEvent `ObjectSuperseded` payload carries `session_id` + id-style keys (guard (g)) and event-catalog.md:18 reflects the single shared emit path (I2).
4. Parity file: differential test deleted; arm-alone retained/renamed as `test_hosted_commit_writes_supersession_end_state`; `test_hosted_commit_wires_apply_supersessions_once` added; module docstring rewritten; file green (I5, REQ 4).
5. `TestE5PointSupersessions` + full `test_commit_endpoint.py` green; capture/eval/projection supersession suites green; `tortoise/commit_ops.py` logic byte-unchanged (I6, REQ 6).
6. Grep sweep clean: no `phase-2) hosted`, no §6b-divergence comments, no stale 6140-citations, no "hosted §6b journals id-only" wording (REQ 5).
7. Worktree implemented off 16075c8f; delta statement shipped in the PR (REQ 7-8).

## 7. Delta statement (post-migration behavior changes — none on the happy path)

- **A→C terminal-conflict re-records stop clobbering (keep-first).** A commit that re-records an already-folded Object with a DIVERGENT successor previously blind-overwrote `supersededBy` (B→C, silent); it now warns through `_logger.warning` and keeps the first fold (B). Same-successor re-records dedup silently (previously re-emitted + re-folded every time).
- **Legacy id-less folds now journal.** §6b's id-less emit (`id=None`) journaled NOWHERE (JSONL early-return; GraphEvent payload `{}`); the helper synthesizes the canonical id (`_entity_name_id`) and journals id-style — rebuild replay now restores these folds including `supersededBy` (M2/C2 provenance closure).
- **ObjectSuperseded GraphEvent payload gains `session_id`** (positional-payload emit → id-style kwargs emit; indicator-2/C2 gap closed at the journal level).
- **Log-message text changes** to the helper phrasing (e.g. `supersession ref ... not found in the graph — skipped (fail-open)` → helper `supersession record skipped (...)` variants; new warns for keep-first conflict, self-alias, never-guess, dangling/not-visible successor). Warning channel unchanged: hosted `_logger.warning` (module attribution preserved).
- **Internal probe shape** changes from per-record single-row `LIMIT 1` probes to the helper's batched `IN` probes + successor-first visibility scan (indicator-3 documented choice). No end-state effect; hosted runs the same probe shape capture/eval run.
- **No happy-path delta:** same folds, same fail-open, same 200 responses, same terminal pt_ silent skip, same payload-deterministic `warnings[]` contract (supersession warnings remain logger-only — the capture-vs-commit client-visibility asymmetry is pre-existing and documented as a known limitation, out of scope).

## 8. Runtime prerequisites

- Docker FalkorDB up: `docker compose -f ../eldato/operations/memory/docker-compose.yml up -d`.
- `export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'` (docker lane is the pytest default — epic #1647).
- `uv sync` in the worktree (min uv 0.6.0; Python 3.12+ per `.python-version`).
- Worktree `../tortoise-wt-2193` on `feat/2193-hosted-supersession-migration` @16075c8f — the dirty local checkout predates #2164 and is never an edit base.
- No dependency changes → `uv lock` untouched.
- Post-review pipeline steps: plan-review gate → `planned` label → execution handoff (≤ 8 tasks → subagent-driven in-session per writing-plans `05-review-handoff.md`).
