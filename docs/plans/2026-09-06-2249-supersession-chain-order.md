<!-- research-path: issue-scoping comment on #2249 (comment 5560116451) + scope-gate external check (competitor/pattern research below) -->

# #2249 — Same-commit supersession chain order-sensitivity: dependency-ordered fold plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `apply_supersessions` fold same-payload supersession chains (A→B, B→C in one payload) in dependency order so the end state is identical regardless of client emission order — while keeping every pinned invariant (guard (h) cross-commit skip, keep-first/dedup, visible-successor gate, payload-literal end state).

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Add a module-level stable topological fold-order helper in `tortoise/commit_ops.py`. It runs as a silent pre-pass before the existing per-record loop, only when a payload carries ≥2 entity-lane records. It resolves, via **two batched graph probes** (never canonical-id math — legacy non-canonical-id carriers make math unsound), the object each record's fold would terminalize (ref side) and the id-carrying carriers under each successor name. A dependency edge R→S is created when S's fold terminalizes an object that is R's successor candidate (R must fold before S terminalizes its successor). A stable Kahn topological sort (min-heap by original index) yields the fold order; cycles + their transitive dependents stay in payload order (deterministic, reproduces today's outcome); any unresolved/ambiguous record contributes no edges (fail-soft to payload order). The main loop body and its fold-time gates run **unchanged** over the sorted order — the gates themselves discriminate same-payload chains (successor live pre-payload → folds) from cross-commit terminal successors (guard (h): pre-payload-terminal → skip), because gates read live status at fold time.

**Pattern Research:** Plan touches **zero third-party dependencies** (pure in-repo helper change in `tortoise/commit_ops.py` + existing test files) → writing-plans Step-B Perplexity gate **skipped** per skip rule. Prior research consumed: (a) 3 scope agents on #2249 (codebase-explorer + solution-framer devil's-advocate + fresh verifier — all converged on O3; verifier mandated **probe, never canonical math**, SCC/payload-order cycle fallback, stability); (b) an external pattern check (this session, user-requested): agent-memory competitors (Mem0/Letta/LangMem/Zep) have **no supersession chains at all** (direct UPDATE/DELETE or bi-temporal invalidation) — this design space is ours; Wikidata deprecation ranks ("deprecated = superseded = automatically non-asserted") validate the visible-successor gate; topological ordering of dependent updates is the standard order-independence mechanism (FalkorDB's own dependency-graph docs); payload-literal per-event folds match the fold's documented contract + rebuild's blind per-event replay + pre-#2193 §6b parity.

### Integration Surface Map

| Surface | Change | Test layer | Failure modes pinned |
|---|---|---|---|
| `apply_supersessions` direct calls (capture `sdk._extract_session_v2` sdk.py:3438, eval ingest_v2.py:366) | sort pre-pass inside helper; call shape unchanged | unit (test_capture_session.py `test_apply_supersessions_*`) | reverse chain skip → now folds; ambiguity/cycle → payload order; gates re-evaluated at fold time |
| Hosted commit endpoint §6b (`hosted_api.py:7444`) | none (sort inside helper; parity spy asserts passthrough) | e2e (test_commit_endpoint.py guard-style) | reverse-order supersessions in one POST converge |
| Journal emit order (ObjectSuperseded lines) | line order = fold order for reverse payloads (was payload order) | unit assert (seq order) | rebuild pass-1b sweep is order-independent — replay convergence untouched (test_object_registered_journal.py fold tests stay green) |
| Keep-first/dedup + guard (h) | untouched code; sort must be stable + gate-status-aware | existing green pins (test_capture_session.py:1654/1711, test_commit_endpoint.py (b)/(c)/(h)) | sort must not convert a pre-payload-terminal successor into a fold |

Bug-pattern flags: order-dependence (the bug); over-edging harmless (fold-order cosmetics only); under-edging fail-soft (payload order); never-guess refs/dup names must contribute no edges; journal-order change is safe (no test pins line order; replay sweep order-independent).

### Verification Plan

Domain: backend. Complexity: standard. No UI/UX (skip ux-verification depth). Verification = unit (direct-helper RED + green pins) + hosted e2e (commit endpoint) + full core suite + ruff + ci_selection integrity. No content/config/research domains.

---

### Task 1: RED + regression-pin direct-helper tests (`tests/test_capture_session.py`)

**Intent:** Pin the #2249 semantics at the shared-helper layer before any implementation: reverse-emitted same-payload chains must converge to the payload-literal fold order end state; the guard-(h) discriminator, cycles, and same-ref stability must NOT move.
**Acceptance:** New tests exist; the chain-convergence RED tests FAIL on current code (reverse arm: applied=1, chain head stays live / middle link skipped); all green-regression pins PASS on current code. RED commit contains tests only and is committed LOCALLY — the PR opens only after Task 2 lands, so the RED state is never CI-tested in isolation (same as #2194/#2295; CI tiered selection would otherwise run test_capture_session.py and fail).
**Files:**
- Test: `tests/test_capture_session.py` (append after `test_apply_supersessions_divergent_successor_keeps_first`, ~line 1770; module already imports `apply_supersessions` per-test + `json`, `pytest`)

**Step 1: Add the discriminating RED tests + regression pins** (full code below — exact test text):

```python
def test_apply_supersessions_chain_converges_both_orders(sdk):
    """#2249 (O3): a same-payload chain (approach-A → approach-B →
    approach-C) must converge to the IDENTICAL end state whether emitted in
    fold order [A→B, B→C] or reverse order [B→C, A→B]. End state is
    PAYLOAD-LITERAL: A.supersededBy='approach-B' (NOT 'approach-C' — each
    event folds its own target; rebuild replays per-event), B.supersededBy=
    'approach-C', C live. Pre-fix the reverse arm gate-skipped A→B (its
    successor B was terminalized by B→C earlier in the payload) leaving A
    LIVE with its A→B fold unjournaled — the order-sensitive divergence.
    Both arms run on fresh objects (per-arm prefixes) and must produce
    byte-identical fold states + applied counts + journal sets."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, records: list[dict]) -> list[str]:
        for n in ("-A", "-B", "-C"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_chain_" + prefix,
                                      warn=warns.append)
        assert applied == 2, \
            f"[{prefix}] both folds must land: {warns}"
        assert warns == [], \
            f"[{prefix}] no skip warnings on a converging chain: {warns}"
        return warns

    fold_records = [
        {"superseded": "fold-A", "supersedes_by": "fold-B",
         "evidence": "b replaces a"},
        {"superseded": "fold-B", "supersedes_by": "fold-C",
         "evidence": "c replaces b"},
    ]
    reverse_records = [
        {"superseded": "rev-B", "supersedes_by": "rev-C",
         "evidence": "c replaces b"},
        {"superseded": "rev-A", "supersedes_by": "rev-B",
         "evidence": "b replaces a"},
    ]
    _run("fold", fold_records)
    _run("rev", reverse_records)
    for prefix in ("fold", "rev"):
        a = _entity_fold_state(proj, prefix + "-A")
        b = _entity_fold_state(proj, prefix + "-B")
        c = _entity_fold_state(proj, prefix + "-C")
        assert a == ("superseded", prefix + "-B"), \
            f"[{prefix}] payload-literal A.supersededBy=B: {a}"
        assert b == ("superseded", prefix + "-C"), \
            f"[{prefix}] B.supersededBy=C: {b}"
        assert c is not None and (c[0] or "live") == "live" \
            and c[1] is None, f"[{prefix}] C must stay live: {c}"
    # journal parity: both arms journaled exactly one ObjectSuperseded per
    # fold (fold-A/rev-A then fold-B/rev-B — no skip line for either).
    assert _object_superseded_events(proj) == 4, \
        "2 folds × 2 arms — one journal line per fold"
    # journal seq order follows FOLD order in the reverse arm (A→B emitted
    # BEFORE B→C) — fold-order emission, not payload order.
    rows = proj.g.query(
        "MATCH (e:GraphEvent {type:'ObjectSuperseded'}) "
        "RETURN e.payload ORDER BY e.seq",
    ).result_set
    names = [json.loads(r[0])["name"] for r in rows]
    assert names == ["fold-A", "fold-B", "rev-A", "rev-B"], names


def test_apply_supersessions_chain_three_link_reverse(sdk):
    """#2249: three-link reverse emission [C→D, B→C, A→B] must land all
    three folds (applied=3), payload-literal: A.sb=B, B.sb=C, C.sb=D, D
    live. PRE-FIX trace (payload order): C→D folds C (C.sb=D); B→C's
    successor C is now terminal → visible-successor gate skips (B stays
    live); A→B folds onto the still-live B (A.sb=B) → applied=2 with A
    folded, B live, C.sb=D — the chain head isn't the loser here, the
    MIDDLE link B→C is (B never folds). POST-FIX the dependency sort folds
    [A→B, B→C, C→D] and all three land."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    for n in ("t3-A", "t3-B", "t3-C", "t3-D"):
        sdk.create_entity("object", n, objectKind="core:strategy")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "t3-C", "supersedes_by": "t3-D",
          "evidence": "d replaces c"},
         {"superseded": "t3-B", "supersedes_by": "t3-C",
          "evidence": "c replaces b"},
         {"superseded": "t3-A", "supersedes_by": "t3-B",
          "evidence": "b replaces a"}],
        session_id="sess_t3", warn=warns.append)
    assert applied == 3, f"all three folds must land: {warns}"
    assert warns == [], f"no skip warnings: {warns}"
    assert _entity_fold_state(proj, "t3-A") == ("superseded", "t3-B")
    assert _entity_fold_state(proj, "t3-B") == ("superseded", "t3-C")
    assert _entity_fold_state(proj, "t3-C") == ("superseded", "t3-D")
    d = _entity_fold_state(proj, "t3-D")
    assert d is not None and (d[0] or "live") == "live" and d[1] is None, d


def test_apply_supersessions_chain_mixed_id_name_forms(sdk):
    """#2249: chains are naturally MIXED-FORM — the extractor emits the
    superseded side as the graph ID when the object pre-exists
    (extractor_v2.py:3021) while supersedes_by is always the NAME
    (commit_schema.py:476). So the chain records are X.supersedes_by = name
    'mx-B' (id-form-free) and Y.superseded = B's canonical id
    obj-<sha26('mx-B')>. Reverse emission must STILL converge. This test
    forces the fold-order pre-pass to resolve BOTH sides against the graph
    (id-form ref + name-form successor) — raw string comparison would miss
    the edge (name != id string)."""
    from tortoise.commit_ops import apply_supersessions
    from tortoise.sdk import _entity_name_id

    proj = sdk._get_proj()
    sdk.create_entity("object", "mx-A", objectKind="core:strategy")
    sdk.create_entity("object", "mx-B", objectKind="core:strategy")
    sdk.create_entity("object", "mx-C", objectKind="core:strategy")
    b_id = _entity_name_id("Object", "mx-B")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": b_id, "supersedes_by": "mx-C",
          "evidence": "c replaces b (id-form ref)"},
         {"superseded": "mx-A", "supersedes_by": "mx-B",
          "evidence": "b replaces a (name-form successor)"}],
        session_id="sess_mx", warn=warns.append)
    assert applied == 2, f"mixed-form chain must converge: {warns}"
    assert warns == [], warns
    assert _entity_fold_state(proj, "mx-A") == ("superseded", "mx-B")
    assert _entity_fold_state(proj, "mx-B") == ("superseded", "mx-C")


def test_apply_supersessions_chain_legacy_noncanonical_mid_node(sdk):
    """#2249: a chain whose middle node carries a NON-canonical id (legacy
    raw-Cypher write with an explicit pre-canonical id — the
    test_status_projection.py:304 registration-id class) must still
    converge. This discriminates PROBE-based fold-order resolution from
    canonical-id MATH (obj-<sha26(name)>): math would fail to see the edge
    (actual id != computed id) and the reverse-order divergence would
    survive. The id-less inverse is pinned separately (legacy id-less
    mid-node record stays skipped — today's semantics)."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "lc-A", objectKind="core:strategy")
    sdk.create_entity("object", "lc-C", objectKind="core:strategy")
    # raw legacy mid-node with an explicit NON-canonical id
    proj.g.query(
        "CREATE (o:Object {id:'github-issue-2249-lc-B', "
        "name:'lc-B', status:'live'})")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "github-issue-2249-lc-B", "supersedes_by": "lc-C",
          "evidence": "c replaces b"},
         {"superseded": "lc-A", "supersedes_by": "lc-B",
          "evidence": "b replaces a"}],
        session_id="sess_lc", warn=warns.append)
    assert applied == 2, f"chain through a non-canonical id must converge: {warns}"
    assert warns == [], warns
    assert _entity_fold_state(proj, "lc-A") == ("superseded", "lc-B")
    assert _entity_fold_state(proj, "lc-B") == ("superseded", "lc-C")
    rows = proj.g.query(
        "MATCH (o:Object {name:'lc-B'}) RETURN o.supersededBy",
    ).result_set
    assert rows and rows[0][0] == "lc-C", rows
```

**Step 2: Add the regression pins (GREEN on current code — must not move):**

```python
def test_apply_supersessions_inpayload_identical_to_guard_h(sdk):
    """#2249 regression pin (the gate IS the discriminator): when B is
    ALREADY terminal BEFORE the payload (folded by an earlier commit —
    guard-(h) semantics) and the payload REDUNDANTLY re-asserts [B→C, A→B],
    A must STAY LIVE (applied=0): A→B sorts first but its fold-time gate
    sees B's PRE-payload terminal status (nothing folded yet) → skip. The
    sort must never turn a pre-payload-terminal successor into a fold."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    sdk.create_entity("object", "gh-A", objectKind="core:strategy")
    sdk.create_entity("object", "gh-B", objectKind="core:strategy")
    sdk.create_entity("object", "gh-C", objectKind="core:strategy")
    # prior commit folds B→C (B terminal pre-payload)
    prior = apply_supersessions(
        proj, sdk,
        [{"superseded": "gh-B", "supersedes_by": "gh-C",
          "evidence": "prior commit"}],
        session_id="sess_gh1")
    assert prior == 1
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "gh-B", "supersedes_by": "gh-C",
          "evidence": "redundant dedup"},
         {"superseded": "gh-A", "supersedes_by": "gh-B",
          "evidence": "records a onto pre-terminal b"}],
        session_id="sess_gh2", warn=warns.append)
    assert applied == 0, f"A must stay live (gate is the discriminator): {warns}"
    a = _entity_fold_state(proj, "gh-A")
    assert a is not None and (a[0] or "live") == "live" and a[1] is None, a
    assert _entity_fold_state(proj, "gh-B") == ("superseded", "gh-C")
    assert _object_superseded_events(proj) == 1, \
        "no second journal — B→C deduped silently, A→B skipped"


def test_apply_supersessions_cycle_deterministic(sdk):
    """#2249 regression pin: a same-payload cycle [A→B, B→A] has no total
    fold order → payload order wins (deterministic): the first-emitted
    claim folds, the second gate-skips (its successor is terminal) with the
    keep-first warn, NO exception, applied=1. Both emission orders
    asserted. Pre-fix behavior, pinned."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, records: list[dict]) -> None:
        for n in ("-A", "-B"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_cyc_" + prefix,
                                      warn=warns.append)
        assert applied == 1, f"[{prefix}] first-emitted claim folds: {warns}"
        assert any("keep-first" in w or "no visible successor" in w
                   for w in warns), f"[{prefix}] second claim warns: {warns}"
    _run("c1", [
        {"superseded": "c1-A", "supersedes_by": "c1-B", "evidence": ""},
        {"superseded": "c1-B", "supersedes_by": "c1-A", "evidence": ""},
    ])
    _run("c2", [
        {"superseded": "c2-B", "supersedes_by": "c2-A", "evidence": ""},
        {"superseded": "c2-A", "supersedes_by": "c2-B", "evidence": ""},
    ])
    assert _entity_fold_state(proj, "c1-A") == ("superseded", "c1-B")
    c1b = _entity_fold_state(proj, "c1-B")
    assert c1b is not None and (c1b[0] or "live") == "live", c1b
    assert _entity_fold_state(proj, "c2-B") == ("superseded", "c2-A")
    c2a = _entity_fold_state(proj, "c2-A")
    assert c2a is not None and (c2a[0] or "live") == "live", c2a


def test_apply_supersessions_same_ref_stability(sdk):
    """#2249 regression pin: same-ref divergent claims in ONE payload
    [A→B, A→C] have no dependency edge (disjoint objects) → STABLE payload
    order keeps first-emitted-wins (A.sb=B); reversed [A→C, A→B] → A.sb=C.
    The sort must not destabilize keep-first within one payload."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, winner: str) -> None:
        for n in ("-A", "-B", "-C"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        records = [{"superseded": prefix + "-A", "supersedes_by": prefix + "-B",
                    "evidence": ""},
                   {"superseded": prefix + "-A", "supersedes_by": prefix + "-C",
                    "evidence": ""}]
        if winner == "C":
            records.reverse()
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_sr_" + prefix,
                                      warn=warns.append)
        assert applied == 1, f"[{prefix}] first-emitted claim folds: {warns}"
        assert _entity_fold_state(proj, prefix + "-A") == (
            "superseded", prefix + "-" + winner), \
            f"[{prefix}] first-emitted claim must win"
    _run("sr1", "B")
    _run("sr2", "C")


def test_apply_supersessions_cycle_chain_entangled_payload_order(sdk):
    """#2249 regression pin (plan-review P2, round 2): a cycle entangled with a
    chain in ONE payload has no total order — the whole block falls back to
    PAYLOAD order (deterministic, emission-literal — pre-fix parity). Two
    permutations, both asserting applied=2 with A.sb=B, B.sb=C, each
    exercising a DIFFERENT skip branch on the cyclic record B→A:
    e1 [A→B, B→A, B→C]: A→B folds A; B→A gate-skips (its successor A is
    recall-excluded — no visible successor warn, B is still live at that
    point); B→C folds B.
    e2 [A→B, B→C, B→A]: A→B folds A; B→C folds B; B→A keep-first-warns
    (its ref B is now terminal with a DIVERGENT stored fold C).
    A future Kahn/leftover change that hoists B→C ahead of A→B (terminalizing
    B first) would silently drop A's fold — these pins lock the fallback.
    (Round-1 e2 emission [B→C, B→A, A→B] was WRONG — payload-order fallback
    folds B→C first and the visible-successor gate keeps A live: that arm
    asserted an unreachable applied=2. Permutations are emission-literal and
    only orders with A→B first can land two folds.)"""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()

    def _run(prefix: str, records: list[dict]) -> None:
        for n in ("-A", "-B", "-C"):
            sdk.create_entity("object", prefix + n,
                              objectKind="core:strategy")
        warns: list[str] = []
        applied = apply_supersessions(proj, sdk, records,
                                      session_id="sess_ecc_" + prefix,
                                      warn=warns.append)
        assert applied == 2, \
            f"[{prefix}] A→B and B→C must fold (payload-order fallback): {warns}"
        assert _entity_fold_state(proj, prefix + "-A") == (
            "superseded", prefix + "-B")
        assert _entity_fold_state(proj, prefix + "-B") == (
            "superseded", prefix + "-C")
    _run("e1", [
        {"superseded": "e1-A", "supersedes_by": "e1-B", "evidence": ""},
        {"superseded": "e1-B", "supersedes_by": "e1-A", "evidence": ""},
        {"superseded": "e1-B", "supersedes_by": "e1-C", "evidence": ""},
    ])
    _run("e2", [
        {"superseded": "e2-A", "supersedes_by": "e2-B", "evidence": ""},
        {"superseded": "e2-B", "supersedes_by": "e2-C", "evidence": ""},
        {"superseded": "e2-B", "supersedes_by": "e2-A", "evidence": ""},
    ])


def test_apply_supersessions_cycle_predecessor_hoisted(sdk):
    """#2249 (plan-review round 3 / second-model P2-1): a record whose fold
    FEEDS a cycle (its successor is a cycle member) is hoisted AHEAD of the
    cycle block by the Kahn pass — deterministic, monotonic, garbage-in-only
    (contradictory cyclic input; hoisting can only ADD folds — the hoisted
    record folds onto the cycle member while it is still live, and never
    removes a fold from the cycle block itself). Payload [A→B, B→A, C→A]:
    C→A folds onto the live A first (applied), then the A↔B cycle block
    folds A→B (B→A gate-skips). End state C.sb=A, A.sb=B, B live, applied=2.
    PRE-FIX (payload order): A→B folds A → B→A and C→A both gate-skip
    (successor A terminal) → applied=1, C stays live. This arm is RED
    pre-fix (discriminates the hoist) — it is NOT a regression pin."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    for n in ("h3-A", "h3-B", "h3-C"):
        sdk.create_entity("object", n, objectKind="core:strategy")
    warns: list[str] = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": "h3-A", "supersedes_by": "h3-B", "evidence": ""},
         {"superseded": "h3-B", "supersedes_by": "h3-A", "evidence": ""},
         {"superseded": "h3-C", "supersedes_by": "h3-A", "evidence": ""}],
        session_id="sess_h3", warn=warns.append)
    assert applied == 2, \
        f"C→A must fold onto the live cycle member before the cycle block: {warns}"
    assert _entity_fold_state(proj, "h3-C") == ("superseded", "h3-A")
    assert _entity_fold_state(proj, "h3-A") == ("superseded", "h3-B")
    b = _entity_fold_state(proj, "h3-B")
    assert b is not None and (b[0] or "live") == "live", b
```

**Step 3: Run the new tests — verify the discrimination**

Run: `cd <worktree> && TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_capture_session.py -k 'chain_converges_both_orders or chain_three_link_reverse or chain_mixed_id_name_forms or chain_legacy_noncanonical_mid_node or cycle_predecessor_hoisted or inpayload_identical_to_guard_h or cycle_deterministic or same_ref_stability or cycle_chain_entangled_payload_order' -v`
Expected: **chain_converges / chain_mixed / chain_legacy FAIL** (reverse arm gate-skips the chain head → applied=1); **chain_three_link FAIL** (pre-fix partial folds → applied=2, middle link B→C skipped); **cycle_predecessor_hoisted FAIL** (pre-fix payload order → applied=1, C never folds); **inpayload / cycle / same_ref / entangled PASS** (regression pins — green pre-fix).

**Step 4: Commit the RED + pins**

```bash
git add tests/test_capture_session.py
git commit -m "test(capture): pin #2249 same-payload chain fold-order convergence (RED: reverse-emission arms)"
```

---

### Task 2: Implement the dependency-ordered fold pre-pass (`tortoise/commit_ops.py`)

**Intent:** Make same-payload chains order-independent at the ONE shared consumer, without touching the per-record gates (they stay byte-identical and remain the same-payload/cross-commit discriminator at fold time).
**Acceptance:** All Task-1 RED tests pass; all 13 existing `test_apply_supersessions_*` + guards (a)–(h) + parity/e2e chain tests stay green; docstring contract updated; no new warnings in single-record payloads.
**Files:**
- Modify: `tortoise/commit_ops.py` (add module helper above `apply_supersessions`; wire the order into the loop; update the docstring chain contract)

**Step 1: Add the `_supersession_fold_order` helper** (module-level, above `apply_supersessions`):

```python
def _supersession_fold_order(proj, records):
    """#2249: stable fold order for same-payload supersession chains.

    A same-payload chain (A→B and B→C in ONE payload) folds correctly only
    when A→B runs BEFORE B→C: B→C terminalizes B, and the fold-time
    visible-successor gate then skips A→B (its successor B is
    recall-excluded) leaving A live with its fold unjournaled. Payload
    emission order is NOT controllable (extractor embeds preserve LLM order;
    hosted §6b processes external client payloads verbatim) — reverse
    emission [B→C, A→B] is a natural outcome. This pre-pass returns an
    order in which every chain record folds while its successor is still
    live, making the end state order-INDEPENDENT.

    Mechanics: resolve, via TWO batched graph probes (never canonical-id
    math — legacy non-canonical-id carriers make obj-<sha26(name)> unsound;
    see test_apply_supersessions_chain_legacy_noncanonical_mid_node), the
    object each entity record's fold would terminalize (ref side, mirroring
    the loop's id-match-wins / single-name / never-guess discipline) and
    the id-carrying carriers under each successor name. Edge R→S when S's
    fold terminalizes an object in R's successor-candidate set. Stable Kahn
    (min-heap by original index) → fold order. Records that never fold
    (missing/self/pt_ lane), unresolved/ambiguous refs, and cycles + their
    transitive DEPENDENTS (records a cycle member points to) contribute no
    edges and keep PAYLOAD order (deterministic, reproduces pre-fix
    outcomes). Records that FEED a cycle (a cycle member is their
    successor) sort AHEAD of it — deterministic + monotonic (hoisting can
    only add folds; pinned by test_apply_supersessions_cycle_predecessor_hoisted).
    Never raises — any doubt
    fails soft to payload order. The main loop re-probes per record at
    fold time (its gates are STATUS-dependent — the pre-pass resolves
    structure only, so no TOCTOU: the Object graph is write-static inside
    apply_supersessions, entities precede every call).
    """
    records = list(records or [])
    n = len(records)
    if n < 2:
        return list(range(n))
    entity = []  # (original_index, ref, supersedes_by) — entity lane only
    for idx, record in enumerate(records):
        ref = str(_sr_attr(record, "superseded") or "").strip()
        supersedes_by = str(_sr_attr(record, "supersedes_by") or "").strip()
        if not ref or not supersedes_by or ref == supersedes_by \
                or ref.startswith("pt_"):
            # missing/self are warned + skipped by the loop; pt_ records
            # ride supersede() (separate lane) — none participate in edges
            continue
        entity.append((idx, ref, supersedes_by))
    if len(entity) < 2:
        return list(range(n))
    # Batch probe 1 — successor-name candidates (id-carrying rows only: an
    # id-less carrier can never be a VISIBLE successor — the loop's
    # has_visible_distinct requires an id).
    sb_names = sorted({sb for _, _, sb in entity})
    cand_rows = proj.g.query(
        "MATCH (o:Object) WHERE o.name IN $names RETURN o.name, o.id",
        params={"names": sb_names}).result_set
    cand_ids: dict[str, set] = {}
    for name, oid in cand_rows:
        if oid:
            cand_ids.setdefault(name, set()).add(oid)
    # Batch probe 2 — ref-side resolution, mirroring the loop's discipline
    # (commit_ops.apply_supersessions): an id-form ref wins (rows whose
    # id == ref); two ids claiming one ref = corruption never-guess; a
    # name-form ref resolves only via a SINGLE carrier (>1 = never-guess).
    # Distilled to the single object each fold would terminalize (its real
    # id — None for legacy id-less targets, which can never be an edge
    # endpoint: the loop folds them by name but they are id-less, and only
    # id-carrying nodes can be visible successors).
    refs_sorted = sorted({ref for _, ref, _ in entity})
    tgt_rows = proj.g.query(
        "MATCH (o:Object) WHERE o.id IN $ids OR o.name IN $names "
        "RETURN o.id, o.name",
        params={"ids": refs_sorted, "names": refs_sorted}).result_set
    by_id: dict[str, list] = {}
    by_name: dict[str, list] = {}
    for oid, name in tgt_rows:
        if oid and oid in refs_sorted:
            by_id.setdefault(oid, []).append((oid, name))
        if name in refs_sorted:
            by_name.setdefault(name, []).append((oid, name))
    target_id: dict[str, str] = {}  # ref -> real id its fold terminalizes
    for ref in refs_sorted:
        if by_id.get(ref):
            if len(by_id[ref]) > 1:  # duplicate id claim — loop never-guesses
                continue
            target_id[ref] = by_id[ref][0][0]
        elif len(by_name.get(ref, [])) == 1:
            target_id[ref] = by_name[ref][0][0] or None  # legacy id-less → None
        # else ambiguous (>1 name) or dangling → the loop skips → no edges
    # Edges over ORIGINAL record indices: R must fold before S when S's
    # fold terminalizes an object R's fold needs visible (S.target ∈ R's
    # successor-name candidates). Plan-review P0: the edge runs NEEDER→
    # TERMINALIZER — the producer record (whose visible-successor gate needs
    # its successor live) sorts BEFORE the record that would terminalize it.
    # Over-edging is harmless (fold-order cosmetics only); under-edging
    # fails soft to payload order. Index-space: entity carries ORIGINAL
    # record indices and edges/indeg are keyed by them, so a payload mixing
    # entity records with pt_/self/missing records (which contribute no
    # edges but occupy positions) wires constraints onto the RIGHT records.
    succ: dict[int, list[int]] = {}
    indeg = [0] * n
    for ridx, _ref, sb in entity:                     # R — supersedes_by sb
        for sidx, s_ref, _s_sb in entity:             # S — terminalizes target_id[s_ref]
            if sidx == ridx:
                continue
            t = target_id.get(s_ref)
            if t and t in cand_ids.get(sb, set()):
                succ.setdefault(ridx, []).append(sidx)
                indeg[sidx] += 1
    # Stable Kahn over ALL n positions (entity members carry the edges;
    # non-entity records have indeg 0 and keep payload positions): pop the
    # lowest original index among ready nodes; leftover (cycles + their
    # transitive dependents) appends in original-index order == payload
    # order for that block (deterministic, pre-fix outcome).
    import heapq
    ready = [i for i in range(n) if indeg[i] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for j in succ.get(i, []):
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(ready, j)
    if len(order) < n:  # cycle block — append leftovers in payload order
        remaining = sorted(set(range(n)) - set(order))
        order.extend(remaining)
    return order
```

**Step 2: Wire the order into `apply_supersessions`**

Replace the loop opener:

```python
    if warn is None:
        warn = _logger.warning
    applied = 0
    for record in records or []:
```

with:

```python
    if warn is None:
        warn = _logger.warning
    applied = 0
    # #2249: same-payload chains fold in DEPENDENCY order (a silent stable
    # pre-pass — payloads with <2 entity records or any resolution doubt
    # fall through to payload order). The per-record gates below re-run
    # unchanged at fold time on LIVE status — they remain the
    # same-payload/cross-commit discriminator (guard (h)). A pre-pass
    # failure (transient graph error) fails SOFT to payload order with one
    # warn — pre-fix partial-progress semantics are preserved exactly (the
    # hosted §6b caller runs this bare; capture wraps the whole call).
    records = list(records or [])
    try:
        fold_order = _supersession_fold_order(proj, records)
    except Exception as exc:  # pragma: no cover - transient graph failure
        warn(f"supersession fold-order pre-pass failed ({exc}) — "
             f"falling back to payload order")
        fold_order = list(range(len(records)))
    for i in fold_order:
        record = records[i]
```

**Step 3: Update the docstring chain contract** (commit_ops.py docstring, the #2249 paragraph):

Replace:
```
    CHAINS (A→B and B→C in one payload) must be emitted in fold order
    ([A→B, B→C]): the visible-successor gate warns and skips a fold whose
    successor this same payload has already terminalized — reverse order
    leaves A live with its A→B fold unjournaled (order-sensitivity tracked
    in #2249; extractor-side emission currently preserves embed/LLM order
    with no sort). Returns the number of
```
with:
```
    CHAINS (A→B and B→C in one payload) fold in DEPENDENCY order regardless
    of emission order (#2249): the pre-pass orders records so each fold
    runs while its successor is still live, and the visible-successor gate
    (which warns + skips a fold whose successor is recall-excluded) stays
    the same-payload-vs-cross-commit discriminator at fold time — a
    successor terminalized EARLIER IN THIS PAYLOAD folds after its
    producer; one terminal BEFORE the payload still skips (guard-(h)
    semantics). Returns the number of
```

**Step 4: Run the direct-helper suite**

Run: `cd <worktree> && TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_capture_session.py -k 'apply_supersessions' -v`
Expected: ALL PASS (13 pre-existing + 9 new — the 5 chain-convergence/hoist RED tests now green + 4 pins — all 22 match the -k scope). Watch: reverse-arm journal seq order asserts fold-order emission.

**Step 5: Guard + parity + e2e green check** (two invocations — the -k filter would otherwise deselect unrelated files)

Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_commit_endpoint.py -k 'Supersession or supersede' -q`
Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_commit_supersession_parity.py tests/test_capture_session_supersession_e2e.py tests/test_status_projection.py tests/test_object_registered_journal.py tests/test_lme_ingest_v2_supersession.py -q`
Expected: ALL PASS — guards (a)–(h) (esp. (h) cross-commit skip), parity spy (records passthrough), chain-pin e2e, fold/replay round-trips unchanged.

**Step 6: Commit**

```bash
git add tortoise/commit_ops.py tests/test_capture_session.py
git commit -m "feat(supersession): fold same-payload chains in dependency order (#2249)"
```

---

### Task 3: Hosted commit-endpoint e2e RED → green (`tests/test_commit_endpoint.py`)

**Intent:** Pin the fix at the hosted §6b layer — the consumer that processes EXTERNAL client payloads (the producer capture can't be disciplined; the endpoint must converge on reverse-emitted chains end-to-end).
**Acceptance:** A single POST carrying a reverse-order chain [B→C, A→B] converges to A.sb=B, B.sb=C, C live; the cross-commit guard (h) test still passes.
**Files:**
- Test: `tests/test_commit_endpoint.py` (add a `test_same_commit_reverse_chain_converges` to `Test6bEntitySupersessionGuards`, after guard (h) ~line 1141)

**Step 1: Add a chain supersession-commit helper + the e2e test** (near `_supersede_commit` / guard (h)):

```python
def _supersede_chain_commit(client, session_id: str, chain: list[tuple[str, str]],
                            *, evidence: str):
    """#2249: a commit whose ONLY supersession work is a same-payload CHAIN
    (reverse-emission shape — the payload asserts B→C before A→B). Rides a
    two-record supersessions list; the test seeds successors first via
    _seed_objects (mirroring the guard (a)–(h) style)."""
    raw = _raw_payload(1, session_id=session_id, supersessions=[
        {"superseded": ref, "supersedes_by": sby, "evidence": evidence}
        for ref, sby in chain
    ])
    return _commit(client, raw)


class Test6bSameCommitChain:  # sibling class — same module `client` fixture scope
    def test_reverse_chain_folds_to_literal_end_state(self, client):
        """(i) #2249 hosted e2e — a same-commit chain emitted REVERSE
        ([B→C, A→B]) must converge to the payload-literal end state: A
        supersededBy h-b, B supersededBy h-c, C live. Pre-fix A stayed live
        (its fold gate-skipped a successor the same payload terminalized)."""
        _seed_objects(client, "g6i-seed", ["h-a", "h-b", "h-c"])
        r = _supersede_chain_commit(
            client, "g6i-c1", [("h-b", "h-c"), ("h-a", "h-b")],
            evidence="reverse chain")
        assert r.status_code == 200, r.text
        a = _object_row("h-a")
        assert a and a[0][0] == "superseded" and a[0][1] == "h-b", a
        b = _object_row("h-b")
        assert b and b[0][0] == "superseded" and b[0][1] == "h-c", b
        c = _object_row("h-c")
        assert c and (c[0] or "live") == "live" and c[1] is None, c
```

**Step 2: Run — verify RED before fix is already green (the fix landed in Task 2)**

Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_commit_endpoint.py -k 'reverse_chain_folds_to_literal_end_state or existing_terminal_successor_skips_fold' -v`
Expected: BOTH PASS (the chain e2e passes because the Task-2 sort is present; guard (h) stays green). The RED moment for this test predates Task 2 — confirm discrimination by stashing the commit_ops change once (`git stash`), observing the reverse-chain e2e FAIL (A live), then `git stash pop`.

**Step 3: Commit**

```bash
git add tests/test_commit_endpoint.py
git commit -m "test(commit): pin hosted reverse-order chain convergence end-to-end (#2249)"
```

---

### Task 4: Full-suite verification + docs contract sweep

**Intent:** Prove no regression across the supersession/journaling surface and leave the in-code #2193 contract comments truthful (order-sensitivity resolved).
**Acceptance:** Core suite green; ruff clean; ci_selection integrity clean; `hosted_api.py:7424` comment no longer mandates client fold-order discipline.
**Files:**
- Modify: `tortoise/hosted_api.py` (the #2193 residue comment ~7424)
- Test: full core suite (no new files → no ci-surfaces registration needed)

**Step 1: Update the §6b in-code comment** (`hosted_api.py`, the block ending ~7426)

Replace:
```
    # supersession chains must be emitted in fold order ([A→B, B→C]) — the
    # visible-successor gate skips a fold whose successor this payload has
    # already terminalized (order-sensitivity pinned in #2249). The step-6
    # entity writes above have landed the payload's net-new successors.
```
with:
```
    # supersession chains fold in dependency order inside apply_supersessions
    # (#2249) — emission order is irrelevant; the helper's pre-pass sorts so
    # each fold runs while its successor is still live. Cross-commit
    # reverse-arriving chains still skip (guard (h) — the fold-time gate
    # discriminates pre-payload terminality). The step-6 entity writes above
    # have landed the payload's net-new successors.
```

**Step 2: Full verification**

Run:
```bash
cd <worktree>
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -q -x
uv run ruff check tortoise/commit_ops.py tortoise/hosted_api.py tests/test_capture_session.py tests/test_commit_endpoint.py
python3 tools/ci_selection.py --integrity
```
Expected: all tests green (no -x failure), ruff clean, integrity clean (no new test files).

**Step 3: Commit**

```bash
git add tortoise/hosted_api.py
git commit -m "docs(commit): supersession chains fold in dependency order — contract comment update (#2249)"
```

---

## Out of scope (tracked siblings)

- **#2242** TOCTOU concurrent-fold CAS — the sort must not assume atomicity or restructure per-record probes (this plan keeps them byte-identical; the pre-pass is read-only structural resolution on a write-static graph).
- **#2243** SupersessionRecord schema caps — no schema/record-model change here.
- Cross-commit reverse-arriving chains (guard (h)) stay skipped **by design** — same-payload chain resolution is the only authorized exception, and the fold-time gate provides it for free.

## Accepted divergences

1. **Pre-pass probes twice (2 batched + per-record loop probes)** — single-record payloads (the universal case) skip the pre-pass entirely; multi-record payloads pay 2 extra batched queries. No TOCTOU (graph write-static inside the call).
2. **Distilled resolution mirror** duplicates ~15 lines of the loop's ref-resolution discipline (by_id-preference / single-name / never-guess) rather than extracting a shared helper — extraction would reopen the fully-reviewed fold path for a 2-use case; the distillation is structural-only (status-free), sits adjacent to the loop, and carries a keep-in-sync comment. Dedup moment if a third consumer appears.
3. **Journal line order** changes payload-order → fold-order for reverse payloads — safe: rebuild pass-1b is a per-event blind sweep (order-independent); no test pins supersession line order across payload reorders (verified).
4. **Cycle + chain entanglement**: records whose fold depends on a cycle member keep payload order with the cycle (deterministic; garbage-in outcome). Records that FEED a cycle (their successor is a cycle member) are hoisted AHEAD of the cycle block by the Kahn pass — deterministic, monotonic (can only ADD folds), pinned by test_apply_supersessions_cycle_predecessor_hoisted (RED pre-fix, applied=1 → post-fix applied=2).

<!-- plan-review: cycles=3, status=clean, version=2.3.0 -->
<!-- plan-review-cycle-1: P0 edge-direction inverted (fixed), P1 index-space mixing (fixed), P2s verbatim fragments/dead-param/eval-surface (fixed) -->
<!-- plan-review-cycle-2: P0 entangled-pin e2 arm unreachable (fixed — emission-literal arms), P2 docstring skip-branch mischaracterization + pin count (fixed) -->
<!-- plan-review-cycle-3: zero P0/P1; P2s 3-link pre-fix narrative + test counts + Step-5 -k gutting journal file (fixed) -->
<!-- second-model-gate: CLEAN of P0/P1; P2 cycle-predecessor hoisting undocumented (fixed + pinned RED), P2 RED-commit CI-safety note (fixed) -->
