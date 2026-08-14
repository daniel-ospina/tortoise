"""Epic 903 shared test fixtures (F1–F5) + hermetic harness (#1250).

Deterministic builders consumed by the dreaming-EP DE2E tests (epic 903-C12,
``docs/epics/2026-08-13-903-dreaming-ep/04-plan.md`` Substep 7). Cross-cutting
test support only — no production code.

Harness rules (fixed per the epic plan review gate):
- **Hermetic embedded pattern** per ``tests/test_dream.py``: tempfile-backed
  ``TortoiseSDK``; every claim is created with ``status="live"`` so the #780
  draft filter (which strips draft inputs from operators, making them
  degenerate) cannot nullify the graph. NOT the Docker-gated
  ``tests/test_ep_directional.py`` pattern.
- **No wall-clock staleness manufacturing anywhere**: F2 stamps
  ``lastDreamedAt`` via DIRECT Cypher SET with FIXED ISO timestamps — a
  sub-second pass would otherwise produce identical stamps → flaky. Nothing in
  this module calls ``time.time()``/``datetime.now()`` to manufacture state.
- **Single-SDK threading only**: embedded redislite is not
  multi-connection-safe (multi-SDK variants are gated to live FalkorDB per the
  conftest #432 note). F4 creates two SDKs on DIFFERENT tempfile paths for the
  sandboxed-clone oracle — never two SDKs on one path.
- **Per-test fresh fixtures** (uuid-namespace/tempfile) → order-independent
  under pytest-randomly.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from dataclasses import dataclass, field

# Make tortoise importable when tests are run as `python -m pytest tests/`
# from the repo root or from inside tests/ (mirrors tests/test_dream.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK  # noqa: E402

# ── Shared constants ────────────────────────────────────────────────

#: Fixed seed for every deterministic EP run / builder (epic number 903).
#: ``ep.run`` shuffles factors with the global RNG, so consumers MUST pin the
#: seed before any EP run to get deterministic trajectories. The corpus graph
#: itself is built explicitly (no randomness), so the oracle converged means
#: are seed-invariant (calibrated: max |Δmean| = 0.0 across seeds 903/1/42/7).
FIXED_SEED = 903

#: Production EP defaults (tortoise/ep.py) — pinned here so the calibration
#: test can assert F3 exhausts the real iteration cap, not a fixture-chosen one.
EP_MAX_ITER = 50
EP_TOL = 1e-4

#: F2 — fixed ISO staleness stamps (direct Cypher SET, never wall-clock).
STAMP_OLD = "2026-01-15T00:00:00+00:00"
STAMP_MEDIUM = "2026-06-01T00:00:00+00:00"
STAMP_FRESH = "2026-08-01T00:00:00+00:00"

#: F5 — pinned diagnostics graph counts (representative synthetic shape).
F5_N_CLAIMS = 40
F5_N_OPERATORS = 12
F5_N_EDGES = 35
#: operator-arity → count (fan-out distribution; sums to F5_N_EDGES).
F5_FAN_OUT = {2: 5, 3: 4, 4: 2, 5: 1}


# ── Hermetic construction helpers ───────────────────────────────────

def fresh_sdk(prefix: str = "tortoise_epic903_") -> tuple[TortoiseSDK, str]:
    """Create a fresh hermetic embedded SDK on a NEW tempfile path.

    Returns ``(sdk, db_path)``. Per-test fresh fixtures (uuid-namespace
    tempfile) → order-independent under pytest-randomly. Callers are
    responsible for ``sdk.close()``.

    Post-#344/#1157, dream() is fail-closed on calibration
    (TORTOISE_EP_REQUIRE_CALIBRATION defaults to "1") — synthetic fixtures
    use statement/observation points as evidence, and calibration posture is
    orthogonal to what the epic-903 tests verify (freshness stamping,
    staleness ranking, warm-start equivalence). Disable it for the hermetic
    harness so fixtures stay deterministic; production posture is untouched
    (env is only defaulted, never forced).
    """
    os.environ.setdefault("TORTOISE_EP_REQUIRE_CALIBRATION", "0")
    db_path = os.path.join(tempfile.mkdtemp(prefix=prefix), "test.db")
    sdk = TortoiseSDK(db_path)
    return sdk, db_path


def _make_claim(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    """#992: EP tests model live claims — create_point defaults to draft since
    #943 (#780 draft filter strips draft inputs from operators, making them
    degenerate)."""
    return sdk.create_point(kind, content, dedup=False, status="live")


def _set_last_dreamed_at(sdk: TortoiseSDK, point_id: str, iso: str) -> None:
    """Manufacture a staleness stamp by DIRECT Cypher SET (#1250 F2).

    Never wall-clock dreaming — a sub-second pass would produce identical
    stamps → flaky. ``lastDreamedAt`` is a plain Point property; the dreaming
    implementation (903-C1/C2) reads it as scheduler fuel.
    """
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.lastDreamedAt = $ts",
        params={"id": point_id, "ts": iso},
    )


# ── F1 — EP-parity corpus ───────────────────────────────────────────

#: 25 IMPL edges forming 6 CONNECTED derivation trees (eval-spec §3 corpus v1
#: shape). Each tree = 1 premise leaf (pN) + N tree claims + N edges (a tree on
#: N+1 nodes has N edges; Σ edges = Σ claims = 25 over the 6 trees).
_CORPUS_IMPL_EDGES: list[tuple[str, str]] = [
    # T1 (4 claims, 4 edges) — chain t1→t2→t3→t4 on premise p1
    ("p1", "t1"), ("t1", "t2"), ("t2", "t3"), ("t3", "t4"),
    # T2 (4 claims, 4 edges) — chain t5→t6→t7→t8 on premise p2
    ("p2", "t5"), ("t5", "t6"), ("t6", "t7"), ("t7", "t8"),
    # T3 (5 claims, 5 edges) — branching tree on premise p3
    ("p3", "t9"), ("t9", "t10"), ("t9", "t11"), ("t10", "t12"), ("t11", "t13"),
    # T4 (4 claims, 4 edges) — chain t14→t15→t16→t17 on premise p4
    ("p4", "t14"), ("t14", "t15"), ("t15", "t16"), ("t16", "t17"),
    # T5 (4 claims, 4 edges) — chain t18→t19→t20→t21 on premise p5
    ("p5", "t18"), ("t18", "t19"), ("t19", "t20"), ("t20", "t21"),
    # T6 (4 claims, 4 edges) — chain t22→t23→t24→t25 on premise p6
    ("p6", "t22"), ("t22", "t23"), ("t23", "t24"), ("t24", "t25"),
]

#: 10 gold contradiction pairs (NAND edges) — endpoints are tree claims.
_CORPUS_NAND_PAIRS: list[tuple[str, str]] = [
    ("t3", "t7"), ("t8", "t13"), ("t16", "t20"), ("t24", "t4"), ("t1", "t17"),
    ("t6", "t18"), ("t11", "t25"), ("t14", "t21"), ("t5", "t12"), ("t19", "t9"),
]

#: 5 near-dup pair contents — n1..n10 are 5 identical-content pairs; n11..n15
#: are unique noise claims (eval-spec §3: 15 noise points).
_CORPUS_DUP_CONTENTS = [
    "noise dup A", "noise dup B", "noise dup C", "noise dup D", "noise dup E",
]

#: premise baseline tiers, cycled (T0 / T1 / T2).
_CORPUS_TIERS = [(10.0, 1.0), (5.0, 1.0), (3.0, 1.0)]


@dataclass(frozen=True)
class F1Corpus:
    """F1 — deterministic EP-parity corpus (eval-spec §3 shape as code).

    Counts (pinned by the calibration test): 60 claims (20 premises + 25 tree
    claims + 15 noise), 25 IMPL edges in 6 derivation trees, 10 NAND
    contradictions, 5 near-dup pairs.
    """

    sdk: TortoiseSDK
    db_path: str
    #: corpus key → live claim id (premises "p1".."p20", tree "t1".."t25",
    #: noise "n1".."n15"). Stable semantic keys let F4's oracle (computed on a
    #: different SDK with different ulid ids) address the same claims.
    claims: dict[str, str] = field(default_factory=dict)
    #: operator corpus key → operator id.
    operators: dict[str, str] = field(default_factory=dict)

    @property
    def n_claims(self) -> int:
        return len(self.claims)

    @property
    def n_impl(self) -> int:
        return sum(1 for k in self.operators if k.startswith("impl"))

    @property
    def n_nand(self) -> int:
        return sum(1 for k in self.operators if k.startswith("nand"))


def _build_corpus(sdk: TortoiseSDK) -> tuple[dict[str, str], dict[str, str]]:
    """Build the F1 corpus on an existing SDK. Returns (claims, operators)."""
    claims: dict[str, str] = {}
    operators: dict[str, str] = {}

    # 20 premises with tiered evidence baselines (calibrated for EP).
    for i in range(1, 21):
        pid = _make_claim(sdk, f"premise {i}")["id"]
        alpha, beta = _CORPUS_TIERS[i % 3]
        sdk.set_point_baseline(pid, alpha, beta)
        claims[f"p{i}"] = pid

    # 25 tree claims.
    for i in range(1, 26):
        claims[f"t{i}"] = _make_claim(sdk, f"tree claim {i}")["id"]

    # 25 IMPL edges forming 6 derivation trees (explicit, fixed order).
    assert len(_CORPUS_IMPL_EDGES) == 25
    for idx, (src, tgt) in enumerate(_CORPUS_IMPL_EDGES, start=1):
        src_id = claims[src]
        op = sdk.create_operator("IMPL", src_id, [claims[tgt]],
                                 direction="bidirectional")
        operators[f"impl_{idx}"] = op["id"]

    # 10 NAND contradiction pairs (bidirectional mutual contradiction).
    assert len(_CORPUS_NAND_PAIRS) == 10
    for idx, (a, b) in enumerate(_CORPUS_NAND_PAIRS, start=1):
        op = sdk.create_operator("NAND", claims[a], [claims[b]],
                                 direction="bidirectional")
        operators[f"nand_{idx}"] = op["id"]

    # 15 noise claims: 10 members of 5 near-dup pairs (identical content) +
    # 5 unique noise.
    for i in range(1, 11):
        content = _CORPUS_DUP_CONTENTS[(i - 1) // 2]
        claims[f"n{i}"] = _make_claim(sdk, content)["id"]
    for i in range(11, 16):
        claims[f"n{i}"] = _make_claim(sdk, f"unique noise {i}")["id"]

    return claims, operators


def f1_corpus(seed: int | None = None) -> F1Corpus:
    """F1 — deterministic EP-parity corpus builder.

    Used by warm-start DE2E-6a/6b. ``seed`` pins the global RNG for any
    downstream EP run (the graph itself is built explicitly); defaults to
    ``FIXED_SEED``.
    """
    random.seed(seed if seed is not None else FIXED_SEED)
    sdk, db_path = fresh_sdk(prefix="tortoise_epic903_f1_")
    claims, operators = _build_corpus(sdk)
    return F1Corpus(sdk=sdk, db_path=db_path, claims=claims, operators=operators)


# ── F2 — staleness fixture ──────────────────────────────────────────

@dataclass(frozen=True)
class StalenessRegion:
    """One disconnected region of the F2 fixture."""

    name: str
    #: live claim ids in this region (support chain c1→c2→c3).
    claims: list[str]
    #: operator ids in this region.
    operators: list[str]
    #: expected fixed ISO stamp, or None for the null-stamp region.
    stamp: str | None


@dataclass(frozen=True)
class F2Staleness:
    """F2 — regions with manufactured ``lastDreamedAt`` (fixed ISO, never
    wall-clock) + one null-stamp region + one operator-less isolated claim."""

    sdk: TortoiseSDK
    db_path: str
    regions: list[StalenessRegion]
    #: the operator-less isolated claim id (no stamp, no operators).
    isolated_claim: str


def f2_staleness_regions() -> F2Staleness:
    """F2 — staleness fixture builder.

    Four DISCONNECTED regions (old / medium / fresh / null), each a 3-claim
    IMPL support chain with 2 operators, plus one operator-less isolated
    claim. ``lastDreamedAt`` is manufactured by DIRECT Cypher SET with fixed
    ISO timestamps (``STAMP_*``); the null region and the isolated claim carry
    NO stamp property. Never wall-clock dreaming.
    """
    sdk, db_path = fresh_sdk(prefix="tortoise_epic903_f2_")

    regions: list[StalenessRegion] = []
    for name, stamp in [
        ("old", STAMP_OLD),
        ("medium", STAMP_MEDIUM),
        ("fresh", STAMP_FRESH),
        ("null", None),
    ]:
        claim_ids = [
            _make_claim(sdk, f"{name} claim 1")["id"],
            _make_claim(sdk, f"{name} claim 2")["id"],
            _make_claim(sdk, f"{name} claim 3")["id"],
        ]
        # support chain c1 → c2 → c3 (2 operators per region).
        op1 = sdk.create_operator("IMPL", claim_ids[0], [claim_ids[1]],
                                  direction="bidirectional")["id"]
        op2 = sdk.create_operator("IMPL", claim_ids[1], [claim_ids[2]],
                                  direction="bidirectional")["id"]
        # evidence baseline on the chain root (calibrated).
        sdk.set_point_baseline(claim_ids[0], 10.0, 1.0)
        if stamp is not None:
            for pid in claim_ids:
                _set_last_dreamed_at(sdk, pid, stamp)
        regions.append(StalenessRegion(name=name, claims=claim_ids,
                                       operators=[op1, op2], stamp=stamp))

    isolated = _make_claim(sdk, "isolated operator-less claim")["id"]
    return F2Staleness(sdk=sdk, db_path=db_path, regions=regions,
                       isolated_claim=isolated)


# ── F3 — fails-to-converge fixture ──────────────────────────────────

@dataclass(frozen=True)
class F3NonConvergent:
    """F3 — oscillating structure verified at calibration to FAIL convergence.

    Topology (mutual/triangle NAND loop with strong opposing pressure):
    - claim A — baseline (10, 1): strongly TRUE
    - claim B — baseline (1, 1): neutral
    - one NAND A→B (bidirectional, base weight 8.0): "at most one of A/B true"
    - two IMPL A→B (bidirectional, weight 1.0 each): "B must match A's level"

    The 2:1 IMPL:NAND force balance against A's strong-true baseline sustains
    a limit cycle: B's mean oscillates ≈0.30↔0.46 (period ~3-4 iterations)
    while A wobbles ≈0.82-0.84, so ``ep.run``'s relative change never drops
    below ``EP_TOL`` within ``EP_MAX_ITER``. Calibrated 2026-08-14 (#1250):
    fails across every seed tested and across all three execution paths
    (``sdk.dream``, ``sdk.dream(full=True)``, ``sdk.compute_confidence``).
    The eval-spec B7 odd-NAND triangle is NOT suitable (it converges trivially
    today — plan Substep 7 note).
    """

    sdk: TortoiseSDK
    db_path: str
    #: "a" / "b" claim ids.
    claims: dict[str, str]
    #: "nand" operator id + "impl" operator ids (list).
    operators: dict[str, str | list[str]]

    @property
    def a_id(self) -> str:
        return self.claims["a"]

    @property
    def b_id(self) -> str:
        return self.claims["b"]


def f3_nonconvergent() -> F3NonConvergent:
    """F3 — fails-to-converge fixture builder.

    The builder creates the graph only (no EP run) — consumers run
    ``sdk.dream(dirty_only=True)`` and must receive ``converged=False`` (the
    calibration test pins this). Pins ``FIXED_SEED`` so downstream EP
    trajectories are reproducible.
    """
    random.seed(FIXED_SEED)
    sdk, db_path = fresh_sdk(prefix="tortoise_epic903_f3_")

    a = _make_claim(sdk, "A: strongly true claim")["id"]
    b = _make_claim(sdk, "B: neutral claim")["id"]
    sdk.set_point_baseline(a, 10.0, 1.0)
    sdk.set_point_baseline(b, 1.0, 1.0)

    nand = sdk.create_operator("NAND", a, [b], direction="bidirectional")["id"]
    impls = [
        sdk.create_operator("IMPL", a, [b], direction="bidirectional")["id"],
        sdk.create_operator("IMPL", a, [b], direction="bidirectional")["id"],
    ]
    return F3NonConvergent(
        sdk=sdk, db_path=db_path,
        claims={"a": a, "b": b},
        operators={"nand": nand, "impl": impls},
    )


# ── F4 — frozen-ground-truth fixture ────────────────────────────────

#: F4 compact graph: region R (mutation target — forced stalest-ranked by
#: DE2E-9), regions S and T (controls). 10 claims + 7 operators.
_F4_IMPL_EDGES: list[tuple[str, str]] = [
    # region R: strong support converging on r5
    ("r1", "r4"), ("r2", "r4"), ("r3", "r5"), ("r4", "r5"),
    # region S: control support
    ("s1", "s3"), ("s2", "s3"),
    # region T: minimal control
    ("t1", "t2"),
]
_F4_BASELINES: dict[str, tuple[float, float]] = {
    "r1": (10.0, 1.0), "r2": (5.0, 1.0), "r3": (3.0, 1.0),
    "s1": (10.0, 1.0), "s2": (5.0, 1.0),
    "t1": (10.0, 1.0),
}


@dataclass(frozen=True)
class F4FrozenTruth:
    """F4 — frozen-ground-truth fixture.

    The converged confidence vector (the oracle) is computed OUT-OF-BAND on a
    SANDBOXED CLONE (a second SDK on a separate tempfile path) — never on the
    live fixture, which must stay untouched so DE2E-9 can mutate it and
    measure staleness error against the oracle. The clone is closed after
    oracle capture (its graph lives only in its tempfile).

    ``oracle`` is keyed by stable corpus keys ("r1".."t2"), so it addresses
    the live fixture's claims via ``ids`` despite the clone's different ulid
    ids. Calibrated: the oracle means are seed-invariant (max |Δmean| = 0.0
    across seeds).
    """

    sdk: TortoiseSDK
    db_path: str
    #: corpus key → live claim id.
    ids: dict[str, str]
    #: corpus key → converged mean (frozen ground truth, clone-computed).
    oracle: dict[str, float]
    #: the sandboxed clone's tempfile path (assert distinct from db_path).
    clone_db_path: str


def _build_f4_graph(sdk: TortoiseSDK) -> tuple[dict[str, str], list[str]]:
    """Build the compact F4 graph on an existing SDK.

    Returns ``(ids, operator_ids)`` — key → claim id, plus every operator id.
    """
    ids: dict[str, str] = {}
    operator_ids: list[str] = []
    # all claims first (create_operator validates inputs exist).
    for key in ["r1", "r2", "r3", "r4", "r5", "s1", "s2", "s3", "t1", "t2"]:
        ids[key] = _make_claim(sdk, f"f4 claim {key}")["id"]
    for key, (alpha, beta) in _F4_BASELINES.items():
        sdk.set_point_baseline(ids[key], alpha, beta)
    for src, tgt in _F4_IMPL_EDGES:
        op = sdk.create_operator("IMPL", ids[src], [ids[tgt]],
                                 direction="bidirectional")
        operator_ids.append(op["id"])
    return ids, operator_ids


def f4_frozen_truth(seed: int | None = None) -> F4FrozenTruth:
    """F4 — frozen-ground-truth fixture builder.

    Builds the same deterministic graph TWICE: the live fixture SDK and a
    sandboxed-clone SDK on a SEPARATE tempfile path. EP converges to the
    oracle on the CLONE ONLY (pinned seed), the clone is closed, and the live
    fixture is returned unmutated and un-run.
    """
    seed = seed if seed is not None else FIXED_SEED

    # Build + converge the SANDBOXED CLONE first (oracle out-of-band). If
    # anything fails here the live fixture is never created — no leak.
    clone_sdk, clone_db = fresh_sdk(prefix="tortoise_epic903_f4_clone_")
    try:
        clone_ids, clone_ops = _build_f4_graph(clone_sdk)
        # Oracle computed out-of-band on the clone with a pinned seed. The
        # clone graph is identical to the live fixture's (deterministic
        # builder) but has different ulid ids — the oracle is keyed by the
        # stable corpus keys so it addresses the live fixture via ``ids``.
        random.seed(seed)
        clone_sdk.compute_confidence(factors=clone_ops,
                                     require_calibration=False)
        # compute_confidence leaves the clone's claims dirty, so the first
        # get_confidence would otherwise fire an UNSEEDED lazy-consistency
        # re-dream mid-capture. Dream explicitly under the same pinned seed
        # so the whole capture trajectory is deterministic, then read.
        random.seed(seed)
        clone_sdk.dream(dirty_only=True)
        oracle = {key: clone_sdk.get_confidence(pid)["mean"]
                  for key, pid in clone_ids.items()}
    finally:
        clone_sdk.close()

    # The live fixture is built AFTER the oracle exists and is returned
    # unmutated and un-run (DE2E-9 mutates it and compares against oracle).
    live_sdk, live_db = fresh_sdk(prefix="tortoise_epic903_f4_live_")
    live_ids, _live_ops = _build_f4_graph(live_sdk)
    return F4FrozenTruth(sdk=live_sdk, db_path=live_db, ids=live_ids,
                         oracle=oracle, clone_db_path=clone_db)


# ── F5 — diagnostics fixture ────────────────────────────────────────

#: (op_type, arity) per operator — pinned: 8 IMPL + 4 NAND = 12 operators.
#: arity = 1 source + (arity-1) targets → edges sum = Σ arity = 35.
_F5_OPERATORS: list[tuple[str, int]] = [
    ("IMPL", 2), ("IMPL", 2), ("IMPL", 3), ("IMPL", 3), ("IMPL", 4),
    ("IMPL", 2), ("IMPL", 3), ("IMPL", 2),
    ("NAND", 2), ("NAND", 5), ("NAND", 3), ("NAND", 4),
]


@dataclass(frozen=True)
class F5Diagnostics:
    """F5 — representative synthetic diagnostics graph with pinned counts.

    Pins: 40 claims (12 operator sources + 23 targets + 5 isolated), 12
    operators, 35 IMPL/NAND edges, fan-out distribution ``F5_FAN_OUT``.
    ``stats`` is computed by querying the graph (see
    ``compute_diagnostics_stats``) — DE2E-10 asserts measurable invariants
    only (counts > 0, fan-out sums to edge count, components emitted).
    """

    sdk: TortoiseSDK
    db_path: str
    stats: dict = field(default_factory=dict)


def _build_diagnostics_graph(sdk: TortoiseSDK) -> None:
    """Build the pinned F5 graph on an existing SDK."""
    sources = [f"s{i}" for i in range(1, 13)]
    targets: list[str] = []
    tidx = 1
    for _op_type, arity in _F5_OPERATORS:
        for _ in range(arity - 1):
            targets.append(f"x{tidx}")
            tidx += 1
    isolated = [f"iso{i}" for i in range(1, 6)]
    source_ids = [_make_claim(sdk, c)["id"] for c in sources]
    target_ids = [_make_claim(sdk, c)["id"] for c in targets]
    for c in isolated:
        _make_claim(sdk, c)

    tidx = 0
    for op_type, arity in _F5_OPERATORS:
        src = source_ids.pop(0)
        op_targets = target_ids[tidx:tidx + arity - 1]
        tidx += arity - 1
        sdk.create_operator(op_type, src, op_targets, direction="bidirectional")


def compute_diagnostics_stats(sdk: TortoiseSDK) -> dict:
    """Query the F5 graph and return measurable invariants for DE2E-10."""
    proj = sdk._get_proj()
    g = proj.g
    n_claims = g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN count(n)").result_set[0][0]
    n_operators = g.query(
        "MATCH (n:Point {is_operator:true}) RETURN count(n)").result_set[0][0]
    n_edges = g.query(
        "MATCH (o:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point) "
        "RETURN count(r)").result_set[0][0]

    fan_rows = g.query(
        "MATCH (o:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point) "
        "RETURN o.id, count(r)").result_set
    fan_out: dict[int, int] = {}
    for _oid, arity in fan_rows:
        fan_out[int(arity)] = fan_out.get(int(arity), 0) + 1

    # Connected components over Point nodes sharing an operator (union-find):
    # each operator-anchored region = operator node + its input claims;
    # region/neighborhood sizes emitted for DE2E-10.
    edge_rows = g.query(
        "MATCH (o:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point) "
        "RETURN o.id, c.id").result_set
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        if x not in parent:
            parent[x] = x
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Group each operator's inputs (source + targets) into one region.
    for oid, cid in edge_rows:
        union(oid, cid)
    # Every claim not touched by any operator is its own singleton region.
    claim_rows = g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN n.id").result_set
    for (cid,) in claim_rows:
        if cid not in parent:
            parent[cid] = cid
    comp_sizes: dict[str, int] = {}
    for node in parent:
        root = find(node)
        comp_sizes[root] = comp_sizes.get(root, 0) + 1

    return {
        "n_claims": int(n_claims),
        "n_operators": int(n_operators),
        "n_edges": int(n_edges),
        "fan_out": dict(sorted(fan_out.items())),
        "n_components": len(comp_sizes),
        "component_sizes": sorted(comp_sizes.values(), reverse=True),
    }


def f5_diagnostics() -> F5Diagnostics:
    """F5 — diagnostics fixture builder (pinned counts + fan-out distribution).

    Real-snapshot runs are optional/skipped in CI (external dependency) — the
    fixture is the representative synthetic shape DE2E-10 asserts invariants
    on.
    """
    sdk, db_path = fresh_sdk(prefix="tortoise_epic903_f5_")
    _build_diagnostics_graph(sdk)
    stats = compute_diagnostics_stats(sdk)
    return F5Diagnostics(sdk=sdk, db_path=db_path, stats=stats)
