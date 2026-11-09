"""W3-b why-suite ADVERSARIAL corpus (#2275 — the v1 follow-up).

The v1 corpus (``corpus_manifest.json``, issue #2100) is same-team authored
on the W4 assembly's OWN supported surface, so it can falsify only the TOTAL
absence of surfacing.  This module is the second, jointly-pinned fixture set:
fixtures that plant the edge-case structures the v1 corpus cannot reach, so a
sub-assembly regression (not just a total outage) moves a standing bar.

What each family plants (and what it makes falsifiable):

* ``near_tie`` — a claim with THREE supports whose persisted posterior means
  are strictly ordered but NEAR-tied (0.700 / 0.705 / 0.720).  The
  dig-deeper ``supports`` pointer must land on the HIGHEST-weight record —
  which is planted LAST, so a "first row wins" assembly targets the wrong
  record.  (v1's supports are spread apart by design; this is the pointer
  PRECEDENCE / ordering arm under real posterior variance.)
* ``multi_nand`` — a claim with THREE NANDers: two medium-severity planted
  first, one high-severity planted last.  The assembly's documented budget
  surfaces at most 2, so full recovery is IMPOSSIBLE here by design
  (``nand_recovery_rate`` is a standing bar that is not structurally 1.0)
  and the severity ordering must still put the HIGH-severity
  counterargument in the surfaced set (``high_severity_nand_recovery`` —
  the discriminating bar: an unsorted assembly surfaces the two mediums and
  scores 0.0).  This is the PARTIAL-CONFLICT-SURFACING arm.
* ``chain`` — an OLD claim that is conflicted (support + NAND) AND the head
  of a TWO-LINK supersede chain (old ← successor ← grand_successor).  The
  ``superseded`` pointer must land on the IMMEDIATE successor, never the
  deepest one, and the resolved (anti-ghost) presentation must still hold.
  This is the SUPERSEDE-CHAIN-STACKED-ON-CONFLICT arm.
* ``precedence`` — a decision point that was later SUPERSEDED: the
  assembly then has four candidate dig-deeper pointers (supports / nand /
  tradeoff / superseded) against the ≤3 cap, so the precedence rule must
  keep the tradeoff pointer and drop the superseded one.  This is the
  POINTER-PRECEDENCE arm (the v1 corpus's named gap).
* ``wide_decision`` — a decision point with FOUR alternatives whose
  EP-favored option is planted THIRD (0.875 > 0.714 > 0.600 > 0.500).  The
  tradeoff pointer must land on the EP-favored alternative, not the first
  alternative.  (v1's decisions are 2-alternative with the favorite planted
  first.)
* ``boundary`` — a claim whose persisted posterior is (2.625, 2.625), i.e.
  Beta variance EXACTLY 0.04 == ``CONTESTED_VARIANCE_THRESHOLD``.  The
  contract is ``variance > threshold`` → NOT contested; a ``>=`` assembly
  reads it as a live dispute.  This is the TIE-HANDLING arm.
* ``clean`` — two clean claims (2 supports, no NAND) as the false-positive
  control inside the adversarial corpus, so the 0-false-positive arm keeps a
  real denominator here too.

Gold discipline is UNCHANGED (DM-9, issue #2100): the corpus manifest is
fixture-side only (a ``gold`` key inside it is a validation error), the gold
is sealed, and both are jointly pinned by ``fixtures_hash`` — scoped per
corpus (#2275) so the v1 hash and its blessed lane are untouched.

Additive gold fields this corpus uses (both optional; v1 gold stays
byte-identical and keeps its defaults):

* ``expected.nand_roles`` — the planted NANDers in PLANT order as
  ``{role, severity}``; the top-``W4_MAX_CONFLICTS`` by severity is the
  expected surfaced set, and the recovery metrics resolve ids through it.
* ``expected.favored_option_role`` — which option role EP favors (v1's
  default is ``option_a``; here the favorite is planted third).

Hermetic: this module is pure spec + planters + rendering + grading
extensions.  The caller opens the throwaway graph (``runner._open_graph``).
"""

from __future__ import annotations

from eval.why_suite import corpus, grading, schema, seeding

ADVERSARIAL = corpus.ADVERSARIAL_CORPUS
ADVERSARIAL_CORPUS = ADVERSARIAL  # alias (readability at call sites)

# ── Corpus identity ────────────────────────────────────────────────────────
CORPUS_PROTOCOL = corpus.PROFILES[ADVERSARIAL].protocol
CORPUS_SEED = corpus.PROFILES[ADVERSARIAL].seed
SOURCE_PIN = (
    "tests/eval/why_suite/adversarial.py (#2275) — the adversarial why-suite "
    "corpus: edge-case structures derived from the W4 assembly's own "
    "documented contracts (budgets, severity ordering, threshold direction, "
    "immediate-successor supersession)"
)

# Per-family topic counts (deterministic topic keys).  21 planted points:
# 19 conflicted + 2 clean controls.
FAMILY_COUNTS: dict[str, int] = {
    "near_tie": 4,
    "multi_nand": 4,
    "chain": 4,
    "wide_decision": 4,
    "precedence": 2,
    "boundary": 3,
    "clean": 2,
}

# Planted role templates (content strings — the resolution target for gold's
# target_role refs, exactly the v1 convention).
PLANT_ROLES: dict[str, dict[str, str]] = {
    "near_tie": {
        "claim": "{topic} belief statement",
        "support_a": "{topic} supporting record alpha",
        "support_b": "{topic} supporting record beta",
        "support_c": "{topic} supporting record gamma",
        "counter": "{topic} counterargument gamma",
    },
    "multi_nand": {
        "claim": "{topic} belief statement",
        "support": "{topic} supporting record alpha",
        "counter_a": "{topic} counterargument one",
        "counter_b": "{topic} counterargument two",
        "counter_c": "{topic} counterargument three",
    },
    "chain": {
        "old": "{topic} belief statement",
        "support": "{topic} supporting record alpha",
        "counter": "{topic} counterargument gamma",
        "successor": "{topic} successor belief",
        "grand_successor": "{topic} terminal successor belief",
    },
    "wide_decision": {
        "decision": "{topic} decision point",
        "support": "{topic} decision support record",
        "option_a": "{topic} alternative one",
        "option_b": "{topic} alternative two",
        "option_c": "{topic} alternative three",
        "option_d": "{topic} alternative four",
        "counter": "{topic} decision counterargument",
    },
    "precedence": {
        "decision": "{topic} decision point",
        "support": "{topic} decision support record",
        "option_a": "{topic} alternative one",
        "option_b": "{topic} alternative two",
        "counter": "{topic} decision counterargument",
        "successor": "{topic} successor belief",
    },
    "boundary": {
        "claim": "{topic} boundary belief statement",
        "support": "{topic} supporting record alpha",
        "counter": "{topic} counterargument gamma",
    },
    "clean": {
        "claim": "{topic} clean belief statement",
        "support_a": "{topic} clean record one",
        "support_b": "{topic} clean record two",
    },
}

# The role whose point id is the GRADED (assembled) point per family.
GRADED_ROLE: dict[str, str] = {
    "near_tie": "claim",
    "multi_nand": "claim",
    "chain": "old",
    "wide_decision": "decision",
    "precedence": "decision",
    "boundary": "claim",
    "clean": "claim",
}

# NANDer roles in PLANT order (== the unsorted Cypher row order a naive
# assembly would take — the naive probe's insertion order).
NAND_ROLE_ORDER: dict[str, list[str]] = {
    "near_tie": ["counter"],
    "multi_nand": ["counter_a", "counter_b", "counter_c"],
    "chain": ["counter"],
    "wide_decision": ["counter"],
    "precedence": ["counter"],
    "boundary": ["counter"],
    "clean": [],
}

# Option roles in PLANT order (the naive tradeoff probe takes the first).
OPTION_ROLE_ORDER: dict[str, list[str]] = {
    "wide_decision": ["option_a", "option_b", "option_c", "option_d"],
    "precedence": ["option_a", "option_b"],
}

# The E2E-7 digest postures this corpus reads from the Lanes table.
NEAR_TIE_SUPPORT_POSTERIORS: dict[str, tuple[float, float]] = {
    "support_a": (7.0, 3.0),  # mean 0.700
    "support_b": (7.05, 2.95),  # mean 0.705
    "support_c": (7.2, 2.8),  # mean 0.720  ← highest weight, planted LAST
}

# Family → the expected dig-deeper pointers (gold, {kind, target_role}).
EXPECTED_KINDS: dict[str, list[dict]] = {
    "near_tie": [
        {"kind": "supports", "target_role": "support_c"},
        {"kind": "nand", "target_role": "counter"},
    ],
    "multi_nand": [
        {"kind": "supports", "target_role": "support"},
        # The high-severity NANDer — the surfaced set must contain it.
        {"kind": "nand", "target_role": "counter_c"},
    ],
    "chain": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
        # IMMEDIATE successor — never the grand_successor.
        {"kind": "superseded", "target_role": "successor"},
    ],
    "wide_decision": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
        # The EP-favored alternative — planted THIRD.
        {"kind": "tradeoff", "target_role": "option_c"},
    ],
    # Mixed-stack precedence: supports + nand + tradeoff + superseded is FOUR
    # candidates against the ≤3 dig-deeper cap, so the superseded pointer is
    # the one dropped — the decided-and-since-superseded point must still
    # answer "which alternative does EP favor?".  (Asserted as an absence in
    # the benchmark test: grading can only assert what MUST appear.)
    "precedence": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
        {"kind": "tradeoff", "target_role": "option_a"},
    ],
    "boundary": [
        {"kind": "supports", "target_role": "support"},
        {"kind": "nand", "target_role": "counter"},
    ],
    "clean": [{"kind": "supports", "target_role": "support_a"}],
}

# Planted NANDers in PLANT order with the severity the ASSEMBLY derives
# (severity = "high" when the counter-claim's persisted EP mean >= 0.6 —
# tortoise/why.py::_severity).  multi_nand plants its HIGH one LAST so the
# severity ordering is load-bearing.
NAND_ROLES: dict[str, list[dict]] = {
    "near_tie": [{"role": "counter", "severity": "high"}],
    "multi_nand": [
        {"role": "counter_a", "severity": "medium"},
        {"role": "counter_b", "severity": "medium"},
        {"role": "counter_c", "severity": "high"},
    ],
    "chain": [{"role": "counter", "severity": "high"}],
    "wide_decision": [{"role": "counter", "severity": "high"}],
    "precedence": [{"role": "counter", "severity": "high"}],
    "boundary": [{"role": "counter", "severity": "high"}],
    "clean": [],
}

FAVORED_OPTION_ROLE: dict[str, str] = {"wide_decision": "option_c"}


def topic_keys() -> dict[str, list[str]]:
    return {
        family: [f"{family}-topic-{i}" for i in range(count)]
        for family, count in FAMILY_COUNTS.items()
    }


def render_manifest() -> dict:
    """The jointly-pinned adversarial corpus manifest (fixture side ONLY)."""
    topics = topic_keys()
    conflicted = sum(len(topics[f]) for f in FAMILY_COUNTS if schema.FAMILY_CONFLICTED[f])
    composition = {family: len(keys) for family, keys in topics.items()}
    composition["conflicted"] = conflicted
    composition["clean"] = len(topics["clean"])
    composition["total"] = sum(len(keys) for keys in topics.values())
    return {
        "corpus": ADVERSARIAL,
        "protocol": CORPUS_PROTOCOL,
        "schema_version": schema.SCHEMA_VERSION,
        "seed": CORPUS_SEED,
        "source": SOURCE_PIN,
        "composition": composition,
        "topics": topics,
        "plant_roles": PLANT_ROLES,
    }


def render_gold() -> dict:
    """The sealed adversarial gold — derived from the family structure table
    (the same discipline as the v1 generator: gold↔manifest drift cannot ship
    silently because both sides render from ONE spec)."""
    topics = topic_keys()
    entries: list[dict] = []
    for family in FAMILY_COUNTS:
        conflicted = schema.FAMILY_CONFLICTED[family]
        resolved = family in schema.FAMILY_RESOLVED
        for topic in topics[family]:
            expected: dict = {
                "conflict_surfacing": schema.FAMILY_EXPECTED_CONTESTED[family],
                "dig_deeper_targets": [dict(t) for t in EXPECTED_KINDS[family]],
                "resolved": resolved,
                "support_chain_sufficient": not resolved,
                "tradeoff_sufficient": family in OPTION_ROLE_ORDER,
            }
            if NAND_ROLES[family]:
                expected["nand_roles"] = [dict(r) for r in NAND_ROLES[family]]
            if family in FAVORED_OPTION_ROLE:
                expected["favored_option_role"] = FAVORED_OPTION_ROLE[family]
            entries.append(
                {
                    "point_id": topic,
                    "family": family,
                    "clean": not conflicted,
                    "expected": expected,
                }
            )
    return {
        "corpus": ADVERSARIAL,
        "protocol": CORPUS_PROTOCOL,
        "schema_version": schema.SCHEMA_VERSION,
        "suite": "why_suite",
        "seed": CORPUS_SEED,
        "entries": entries,
    }


# ── Seeding (planters) ────────────────────────────────────────────────────


def _plant_near_tie(sdk, proj, topic: str) -> dict[str, str]:
    """Near-tied supports: three records whose persisted posterior means are
    strictly ordered but close, with the STRONGEST planted last (the
    pointer-precedence arm)."""
    roles: dict[str, str] = {}
    for role in ("support_a", "support_b", "support_c"):
        pid = seeding._create_point(sdk, "evidence", PLANT_ROLES["near_tie"][role].format(topic=topic))
        seeding._set_posterior(proj, pid, *NEAR_TIE_SUPPORT_POSTERIORS[role])
        roles[role] = pid
    claim = seeding._create_point(
        sdk, "statement", PLANT_ROLES["near_tie"]["claim"].format(topic=topic)
    )
    for role in ("support_a", "support_b", "support_c"):
        seeding._imply(sdk, roles[role], claim)
    counter = seeding._create_point(
        sdk, "statement", PLANT_ROLES["near_tie"]["counter"].format(topic=topic)
    )
    seeding._nand(sdk, counter, claim)
    seeding._set_posterior(proj, claim, 2.0, 2.0)  # variance 0.05 → contested
    seeding._set_posterior(proj, counter, 6.0, 1.0)  # mean 0.857 → high severity
    roles["claim"] = claim
    roles["counter"] = counter
    return roles


def _plant_multi_nand(sdk, proj, topic: str) -> dict[str, str]:
    """A three-NAND cluster: two medium-severity counterarguments planted
    first, one high-severity planted last — wider than the ≤2 surfacing cap
    (partial-conflict-surfacing + severity-ordering arm)."""
    support = seeding._create_point(
        sdk, "evidence", PLANT_ROLES["multi_nand"]["support"].format(topic=topic)
    )
    claim = seeding._create_point(
        sdk, "statement", PLANT_ROLES["multi_nand"]["claim"].format(topic=topic)
    )
    seeding._imply(sdk, support, claim)
    posteriors = {
        "counter_a": (1.0, 1.0),  # mean 0.5  → medium
        "counter_b": (1.5, 1.5),  # mean 0.5  → medium
        "counter_c": (6.0, 1.0),  # mean 0.857 → high (planted LAST)
    }
    roles: dict[str, str] = {"claim": claim, "support": support}
    for role in NAND_ROLE_ORDER["multi_nand"]:
        counter = seeding._create_point(
            sdk, "statement", PLANT_ROLES["multi_nand"][role].format(topic=topic)
        )
        seeding._nand(sdk, counter, claim)
        seeding._set_posterior(proj, counter, *posteriors[role])
        roles[role] = counter
    seeding._set_posterior(proj, claim, 2.0, 2.0)  # contested
    seeding._set_posterior(proj, support, 12.0, 1.0)
    return roles


def _plant_chain(sdk, proj, topic: str) -> dict[str, str]:
    """A conflicted claim at the head of a TWO-LINK supersede chain:
    old ← successor ← grand_successor (supersede-chain-stacked-on-conflict
    arm — the pointer must land on the IMMEDIATE successor)."""
    support = seeding._create_point(
        sdk, "evidence", PLANT_ROLES["chain"]["support"].format(topic=topic)
    )
    old = seeding._create_point(sdk, "statement", PLANT_ROLES["chain"]["old"].format(topic=topic))
    seeding._imply(sdk, support, old)
    counter = seeding._create_point(
        sdk, "statement", PLANT_ROLES["chain"]["counter"].format(topic=topic)
    )
    seeding._nand(sdk, counter, old)
    successor = seeding._create_point(
        sdk, "statement", PLANT_ROLES["chain"]["successor"].format(topic=topic)
    )
    grand = seeding._create_point(
        sdk, "statement", PLANT_ROLES["chain"]["grand_successor"].format(topic=topic)
    )
    seeding._set_posterior(proj, old, 2.0, 2.0)  # contested structure pre-terminal
    seeding._set_posterior(proj, support, 12.0, 1.0)
    seeding._set_posterior(proj, counter, 6.0, 1.0)  # high severity
    seeding._set_posterior(proj, successor, 10.0, 1.0)
    seeding._set_posterior(proj, grand, 11.0, 1.0)
    proj.g.query(
        "MATCH (n:Point {id:$old}), (s:Point {id:$new}) "
        "CREATE (s)-[:CORRECTS]->(n) SET n.status='superseded', "
        "n.outdated=true, n.validTo='2026-06-01'",
        params={"old": old, "new": successor},
    )
    proj.g.query(
        "MATCH (n:Point {id:$old}), (s:Point {id:$new}) "
        "CREATE (s)-[:CORRECTS]->(n) SET n.status='superseded', "
        "n.outdated=true, n.validTo='2026-07-01'",
        params={"old": successor, "new": grand},
    )
    return {
        "old": old,
        "support": support,
        "counter": counter,
        "successor": successor,
        "grand_successor": grand,
    }


def _plant_wide_decision(sdk, proj, topic: str) -> dict[str, str]:
    """A decision point with FOUR mitigated alternatives whose EP-favored
    option is planted THIRD (v1's decisions are 2-alternative with the
    favorite first)."""
    support = seeding._create_point(
        sdk, "evidence", PLANT_ROLES["wide_decision"]["support"].format(topic=topic)
    )
    decision = seeding._create_point(
        sdk, "decision", PLANT_ROLES["wide_decision"]["decision"].format(topic=topic)
    )
    seeding._imply(sdk, support, decision)
    posteriors = {
        "option_a": (3.0, 2.0),  # 0.600
        "option_b": (2.0, 2.0),  # 0.500
        "option_c": (7.0, 1.0),  # 0.875 ← EP favorite, planted THIRD
        "option_d": (5.0, 2.0),  # 0.714
    }
    mitigations = {
        "option_a": "stage the migration behind a flag",
        "option_b": "keep the fallback path warm",
        "option_c": "QA gate + staged rollout",
        "option_d": "communicate the delay",
    }
    roles: dict[str, str] = {"decision": decision, "support": support}
    for role in OPTION_ROLE_ORDER["wide_decision"]:
        option = seeding._create_point(
            sdk, "option", PLANT_ROLES["wide_decision"][role].format(topic=topic)
        )
        op = sdk.create_operator("IMPL", decision, [option])
        sdk.mitigate_operator(op["id"], mitigations[role])
        seeding._set_posterior(proj, option, *posteriors[role])
        roles[role] = option
    counter = seeding._create_point(
        sdk, "statement", PLANT_ROLES["wide_decision"]["counter"].format(topic=topic)
    )
    seeding._nand(sdk, counter, decision)
    seeding._set_posterior(proj, counter, 6.0, 1.0)  # high severity
    seeding._set_posterior(proj, decision, 2.0, 2.0)  # contested
    seeding._set_posterior(proj, support, 12.0, 1.0)
    roles["counter"] = counter
    return roles


def boundary_posterior() -> tuple[float, float]:
    """The at-threshold posterior: Beta(2.625, 2.625) has variance EXACTLY
    0.04 — ``CONTESTED_VARIANCE_THRESHOLD``.  The contract is
    ``variance > threshold``, so this point is NOT contested; a ``>=``
    assembly reads it as one.  The exactness is asserted at plant time
    (``_assert_boundary_variance``) so a plant edit cannot quietly move the
    fixture off the boundary and turn the arm into a tautology."""
    return (2.625, 2.625)


def _boundary_variance() -> float:
    alpha, beta = boundary_posterior()
    s = alpha + beta
    return (alpha * beta) / (s * s * (s + 1))


def _assert_boundary_variance() -> None:
    variance = _boundary_variance()
    if variance != schema.CONTESTED_VARIANCE_THRESHOLD:
        raise ValueError(
            "boundary plant is OFF the contested threshold: variance "
            f"{variance!r} != {schema.CONTESTED_VARIANCE_THRESHOLD!r} — the "
            "tie-handling arm only bites AT the boundary (a plant edit must "
            "not silently move it)"
        )


def _plant_boundary(sdk, proj, topic: str) -> dict[str, str]:
    """A claim at variance == the contested threshold exactly, carrying one
    NAND (so the conflicts DIMENSION is computed and only its ``contested``
    flag can be wrong — the tie-handling arm)."""
    _assert_boundary_variance()
    alpha, beta = boundary_posterior()
    support = seeding._create_point(
        sdk, "evidence", PLANT_ROLES["boundary"]["support"].format(topic=topic)
    )
    claim = seeding._create_point(
        sdk, "statement", PLANT_ROLES["boundary"]["claim"].format(topic=topic)
    )
    seeding._imply(sdk, support, claim)
    counter = seeding._create_point(
        sdk, "statement", PLANT_ROLES["boundary"]["counter"].format(topic=topic)
    )
    seeding._nand(sdk, counter, claim)
    seeding._set_posterior(proj, claim, alpha, beta)  # variance == 0.04 exactly
    seeding._set_posterior(proj, support, 12.0, 1.0)
    seeding._set_posterior(proj, counter, 6.0, 1.0)
    return {"claim": claim, "support": support, "counter": counter}


def _plant_precedence(sdk, proj, topic: str) -> dict[str, str]:
    """A decision point that was later superseded: the dig-deeper assembly
    then has FOUR candidate pointers (supports / nand / tradeoff /
    superseded) against the ≤3 cap, so the precedence rule must keep the
    tradeoff pointer and drop the superseded one (mixed-stack precedence)."""
    support = seeding._create_point(
        sdk, "evidence", PLANT_ROLES["precedence"]["support"].format(topic=topic)
    )
    decision = seeding._create_point(
        sdk, "decision", PLANT_ROLES["precedence"]["decision"].format(topic=topic)
    )
    seeding._imply(sdk, support, decision)
    posteriors = {"option_a": (4.0, 1.0), "option_b": (5.0, 2.0)}  # 0.8 vs ~0.714
    mitigations = {
        "option_a": "QA gate + staged rollout",
        "option_b": "communicate the delay",
    }
    roles: dict[str, str] = {"decision": decision, "support": support}
    for role in OPTION_ROLE_ORDER["precedence"]:
        option = seeding._create_point(
            sdk, "option", PLANT_ROLES["precedence"][role].format(topic=topic)
        )
        op = sdk.create_operator("IMPL", decision, [option])
        sdk.mitigate_operator(op["id"], mitigations[role])
        seeding._set_posterior(proj, option, *posteriors[role])
        roles[role] = option
    counter = seeding._create_point(
        sdk, "statement", PLANT_ROLES["precedence"]["counter"].format(topic=topic)
    )
    seeding._nand(sdk, counter, decision)
    seeding._set_posterior(proj, counter, 6.0, 1.0)  # high severity
    seeding._set_posterior(proj, decision, 2.0, 2.0)
    seeding._set_posterior(proj, support, 12.0, 1.0)
    successor = seeding._create_point(
        sdk, "statement", PLANT_ROLES["precedence"]["successor"].format(topic=topic)
    )
    seeding._set_posterior(proj, successor, 10.0, 1.0)
    proj.g.query(
        "MATCH (n:Point {id:$old}), (s:Point {id:$new}) "
        "CREATE (s)-[:CORRECTS]->(n) SET n.status='superseded', "
        "n.outdated=true, n.validTo='2026-06-01'",
        params={"old": decision, "new": successor},
    )
    roles["counter"] = counter
    roles["successor"] = successor
    return roles


def _plant_clean(sdk, proj, topic: str) -> dict[str, str]:
    """The in-corpus false-positive control (v1's clean plant, re-used
    verbatim so the arm's denominator is real in BOTH corpora)."""
    return seeding._plant_clean_roles(sdk, proj, topic)


PLANTERS = {
    "near_tie": _plant_near_tie,
    "multi_nand": _plant_multi_nand,
    "chain": _plant_chain,
    "wide_decision": _plant_wide_decision,
    "precedence": _plant_precedence,
    "boundary": _plant_boundary,
    "clean": _plant_clean,
}


def seed_corpus(sdk) -> dict:
    """Plant the adversarial corpus.  Same return contract as
    ``seeding.seed_why_corpus`` (roles / graded / manifest_topics)."""
    manifest = corpus.load_manifest(corpus=ADVERSARIAL)
    proj = sdk._get_proj()
    roles: dict[str, dict[str, str]] = {}
    graded: dict[str, list[str]] = {}
    topics = manifest["topics"]
    for family in FAMILY_COUNTS:
        planter = PLANTERS[family]
        graded_key = GRADED_ROLE[family]
        graded_list: list[str] = []
        for topic in topics[family]:
            planted = planter(sdk, proj, topic)
            roles[topic] = planted
            graded_list.append(planted[graded_key])
        graded[family] = graded_list
    return {"roles": roles, "graded": graded, "manifest_topics": topics}


def seed_topic(sdk, topic: str) -> dict[str, str]:
    """Plant ONE adversarial topic (used by tests)."""
    manifest = corpus.load_manifest(corpus=ADVERSARIAL)
    proj = sdk._get_proj()
    for family, keys in manifest["topics"].items():
        if topic in keys:
            return PLANTERS[family](sdk, proj, topic)
    raise ValueError(f"topic {topic!r} is not in the adversarial manifest")


# ── Expectation resolution + grading (the adversarial arms) ───────────────


def resolve_expected(gold_entry: dict, role_map_entry: dict) -> dict:
    """v1 resolution + the adversarial extras (planted NAND ids in PLANT
    order with their severity, and the EP-favored option role)."""
    expected = grading.resolve_expected(gold_entry, role_map_entry)
    raw = gold_entry.get("expected") or {}
    nand_roles = raw.get("nand_roles") or []
    planted_nands: list[dict] = []
    for spec in nand_roles:
        role = spec.get("role")
        point_id = (role_map_entry or {}).get(role)
        if not point_id:
            raise ValueError(
                f"gold {gold_entry.get('point_id')!r} declares planted NAND role "
                f"{role!r} but the planted role map has no such id — corpus/gold "
                "inconsistency (never a silent skip)"
            )
        planted_nands.append({"point_id": point_id, "severity": spec.get("severity")})
    favored_role = raw.get("favored_option_role") or "option_a"
    favored_id = (role_map_entry or {}).get(favored_role)
    if bool(expected.get("expected_tradeoff")) and not favored_id:
        raise ValueError(
            f"gold {gold_entry.get('point_id')!r} expects tradeoff sufficiency "
            f"but role {favored_role!r} was not planted — the EP-favored "
            "alternative cannot be resolved"
        )
    expected["favored_option_id"] = favored_id
    expected["favored_option_role"] = favored_role
    expected["expected_nands"] = planted_nands
    return expected


def _surfaced_nand_ids(block: dict) -> list[str]:
    conflicts = block.get("conflicts")
    if not isinstance(conflicts, dict):
        return []
    return [n.get("point_id") for n in (conflicts.get("nands") or []) if n.get("point_id")]


def _reads_contested(block: dict) -> bool:
    """The ONE question the threshold arm grades: does the surfaced context
    present the point as a LIVE dispute (either contested flag)?"""
    ep = block.get("ep")
    if isinstance(ep, dict) and ep.get("contested") is True:
        return True
    conflicts = block.get("conflicts")
    return isinstance(conflicts, dict) and conflicts.get("contested") is True


def grade_point(block: dict, expected: dict) -> dict:
    """v1 grade row + the adversarial arms (NAND recovery, severity-priority
    recovery, contested classification).  Every extra field is derived from
    the surfaced context ALONE — A11 still holds."""
    row = grading.grade_point(block, expected)
    planted = expected.get("expected_nands") or []
    surfaced = _surfaced_nand_ids(block)
    surfaced_set = set(surfaced)
    planted_ids = [p["point_id"] for p in planted]
    # Live arm: a RESOLVED (superseded) claim is not a live dispute — its
    # NANDs are graded by dig-deeper navigation instead (the #2490 contract).
    live = not expected.get("expected_resolved")
    row["nand_planted"] = len(planted_ids) if live else 0
    row["nand_recovered"] = (
        len(surfaced_set & set(planted_ids)) if live else 0
    )
    # Severity-priority arm: over-cap clusters must still surface EVERY
    # high-severity NANDer (the ordering the ≤2 cap relies on).
    over_cap = live and len(planted_ids) > schema.W4_MAX_CONFLICTS
    high_ids = [p["point_id"] for p in planted if p.get("severity") == "high"]
    row["over_cap_cluster"] = over_cap
    row["high_severity_recovered"] = (
        all(pid in surfaced_set for pid in high_ids) if (over_cap and high_ids) else None
    )
    row["expected_contested"] = bool(expected.get("expected_conflict"))
    row["reads_contested"] = _reads_contested(block)
    # The pointer KINDS are surfaced so the precedence arm (which is about an
    # ABSENCE — the ≤3 cap must drop the superseded pointer) is gradable from
    # the run report; the per-kind TARGETS stay graded by navigation.
    row["pointer_kinds"] = [
        p.get("kind") for p in (block.get("dig_deeper") or []) if p.get("kind")
    ]
    return row


def grade_corpus(
    sdk, seed_result: dict, gold: dict, *, naive: bool = False, log: list[str] | None = None
) -> list[dict]:
    """Assemble + grade every planted adversarial point.

    ``naive=True`` grades the same graph through the NAIVE-assembly probe
    (``degrade_to_naive``) instead of the real surfaced context — the
    discrimination measurement: the corpus must score the real assembly at
    the bars and the naive one BELOW them, or the fixtures prove nothing.
    """
    if not naive:
        from eval.why_suite import runner

        return runner.grade_all_points(
            sdk, seed_result, gold, log=log, corpus_name=ADVERSARIAL
        )
    from tortoise.why import assemble_why_blocks

    proj = sdk._get_proj()
    roles = seed_result["roles"]
    entries = gold.get("entries") or []
    graded: dict[str, str] = {}
    for entry in entries:
        role = GRADED_ROLE[entry["family"]]
        graded[entry["point_id"]] = roles[entry["point_id"]][role]
    blocks = assemble_why_blocks(proj, sorted(set(graded.values()))) or {}
    rows: list[dict] = []
    for entry in entries:
        topic = entry["point_id"]
        point_id = graded[topic]
        block = dict(blocks.get(point_id) or {})
        block.setdefault("point_id", point_id)
        expected = resolve_expected(entry, roles[topic])
        naive_block = degrade_to_naive(block, expected, roles[topic], entry["family"])
        row = grade_point(naive_block, expected)
        row["topic"] = topic
        row["point_id"] = point_id
        rows.append(row)
    return rows


# ── The naive-assembly discrimination probe ──────────────────────────────
# A "naive" assembly is one that takes the rows the graph returns in
# insertion order instead of applying the assembly's documented selection
# rules.  This probe degrades REAL assembled blocks into that naive posture so
# the harness can PROVE the corpus discriminates: the real blocks meet the
# standing bars, the naive ones fall below them.  Without this, a bar of 1.0
# could be 1.0 because nothing in the corpus could ever move it.


def degrade_to_naive(block: dict, expected: dict, role_map_entry: dict, family: str) -> dict:
    """Return ``block`` degraded to the naive-assembly posture.

    Four mechanical degradations (one per planted edge case):
    1. conflicts: the FIRST-PLANTED ``W4_MAX_CONFLICTS`` NANDers (insertion
       order, no severity sort) replace the assembly's severity-selected set;
       the nand pointer follows the first planted one.
    2. contested: ``variance >= threshold`` instead of ``>`` (the boundary
       fixture flips).
    3. supersession: the pointer targets the DEEPEST successor when the
       plant is a chain.
    4. tradeoff: the pointer targets the FIRST-PLANTED alternative instead
       of the EP-favored one.
    """
    import copy

    naive = copy.deepcopy(block)
    # 1. NAND selection in plant order.
    nand_order = NAND_ROLE_ORDER.get(family) or []
    planted_ids = [role_map_entry.get(r) for r in nand_order]
    planted_ids = [pid for pid in planted_ids if pid]
    if planted_ids:
        keep = planted_ids[: schema.W4_MAX_CONFLICTS]
        conflicts = naive.get("conflicts")
        if isinstance(conflicts, dict):
            by_id = {n.get("point_id"): n for n in (conflicts.get("nands") or [])}
            conflicts["nands"] = [
                by_id.get(pid) or {"point_id": pid, "content_snippet": "", "severity": "medium"}
                for pid in keep
            ]
        for pointer in naive.get("dig_deeper") or []:
            if pointer.get("kind") == "nand":
                pointer["target"] = keep[0]
    # 2. Threshold direction (>= instead of >).
    ep = naive.get("ep")
    if isinstance(ep, dict) and isinstance(ep.get("variance"), (int, float)):
        at_or_over = ep["variance"] >= schema.CONTESTED_VARIANCE_THRESHOLD
        ep["contested"] = bool(ep.get("has_ep")) and at_or_over
        conflicts = naive.get("conflicts")
        if isinstance(conflicts, dict):
            conflicts["contested"] = ep["contested"]
    # 3. Deepest successor instead of the immediate one.
    deepest = role_map_entry.get("grand_successor")
    if deepest:
        for pointer in naive.get("dig_deeper") or []:
            if pointer.get("kind") == "superseded":
                pointer["target"] = deepest
        supersession = naive.get("supersession")
        if isinstance(supersession, dict) and isinstance(supersession.get("superseded_by"), dict):
            supersession["superseded_by"]["point_id"] = deepest
    # 4. First-planted alternative instead of the EP-favored one.
    option_roles = OPTION_ROLE_ORDER.get(family) or []
    first_option = role_map_entry.get(option_roles[0]) if option_roles else None
    if first_option:
        for pointer in naive.get("dig_deeper") or []:
            if pointer.get("kind") == "tradeoff":
                pointer["target"] = first_option
    return naive
