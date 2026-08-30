"""Enforcement seam tests (#1934, epic #1891 slice 3; test-design #1898
surface 10).

Covers:
- resolve_enforcement: the ladder is consumed (no longer dead config) —
  retry for agent-ops:rule (extraction.enforcement.kinds), warn default,
  block reserved
- create_operator warn-not-block: an undeclared relation label warns
  (structured, in the result) + the write proceeds; a declared pack
  relation label does NOT warn; the violations event fires on the warn path
- Classifier near-miss retry signal: a retry-declared kind in a near-miss
  pair records near_miss_retries (the extractor's M3 loop bounds the actual
  re-attempt)
- Chain rewire BEHAVIOR unchanged for non-enforcement packs (the regression
  guard: graph-visible outcomes, not byte-identical serialization)

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import os
import types

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()


def _two_points(sdk):
    a = sdk.create_point("statement", "the strategy is durable")["id"]
    b = sdk.create_point("statement", "the strategy is not durable")["id"]
    return a, b


# ── resolve_enforcement (the seam — the ladder is consumed) ────────────────

class TestResolveEnforcement:
    def test_retry_for_agent_ops_rule(self):
        """agent-ops declares extraction.enforcement.kinds rule: retry — the
        seam must resolve it (was dead config before #1934)."""
        from tortoise.enforcement import resolve_enforcement
        assert resolve_enforcement(kind="rule") == "retry"

    def test_namespaced_agent_ops_rule_resolves_retry(self):
        """#2030 — the classifier passes the NAMESPACED index kind
        (agent-ops:rule); the seam must resolve the local name against the
        declaring pack (extraction.enforcement.kinds rule: retry). Fails on
        the pre-fix code (bare-keyed lookups miss the namespaced string)."""
        from tortoise.enforcement import resolve_enforcement
        assert resolve_enforcement(kind="agent-ops:rule") == "retry"

    def test_namespaced_unknown_namespace_degrades_to_warn(self):
        """#2030 pin (a) — unknown/malformed namespaces degrade to warn,
        NEVER KeyError (there is no 'core' pack in the registry; nearMisses
        resolution can surface core:* kinds). Empty local (agent-ops:) is
        hard-warn by contract (resolve_enforcement returns warn before any
        pack lookup when the local name is empty)."""
        from tortoise.enforcement import resolve_enforcement
        assert resolve_enforcement(kind="core:occurrence") == "warn"
        assert resolve_enforcement(kind="no-such-ns:x") == "warn"
        assert resolve_enforcement(kind="agent-ops:") == "warn"  # empty local — hard-warn
        assert resolve_enforcement(kind="a:b:c") == "warn"  # multi-colon → ns 'a:b' absent

    def test_namespaced_declaring_pack_only(self):
        """#2030 pin (b) content-level guard — a namespaced kind resolves
        against its declaring pack only: dev:issue is warn (dev declares no
        retry) even though bare 'issue' is collided across dev/pm.
        Non-discriminating against TODAY's manifests (both dev and pm
        resolve bare 'issue' to warn); the STRUCTURAL
        declaring-pack-exclusive guarantee lives in
        test_namespaced_declaring_pack_exclusive_synthetic — this one pins
        the live content and fails loudly if a pack ever declares a higher
        level for a collided bare name."""
        from tortoise.enforcement import resolve_enforcement
        assert resolve_enforcement(kind="dev:issue") == "warn"

    def test_namespaced_declaring_pack_exclusive_synthetic(self, tmp_path, monkeypatch):
        """#2030 pin (b) STRUCTURAL guard, independent of manifest content:
        two packs declaring the SAME bare kind at different levels — the
        declaring pack's own level wins (a:widget retry; b:widget warn even
        though pack 'a' has retry); an invalid declared level falls to warn
        via the VALID_LEVELS guard. RED on pre-fix code (the bare path
        misses the full-namespaced key → a:widget resolves warn)."""
        from tortoise.enforcement import resolve_enforcement
        from tortoise.pack_registry import PackManifest

        def mk(ns: str, level: str, kind: str = "widget"):
            return PackManifest(
                namespace=ns, name=ns, version="0.1.0", tier="free",
                description=ns, path=tmp_path,
                extraction={"enforcement": {
                    "default": "warn",
                    "kinds": {kind: level},
                    "relations": {}, "chains": {},
                }},
            )

        pa, pb, pc, pd = mk("a", "retry"), mk("b", "warn"), mk("c", "nuke"), mk("d", "block")
        # retry>warn ranking WITHOUT the block early-exit: warn-first
        # insertion order on a distinct kind. NOTE: pack e's retry DEFAULT
        # also participates in the bare scan (it is a per-pack resolution
        # leg), so this pins scan direction / max-aggregation more than a
        # pure comparator isolation — the assertion value stays exact.
        pf, pg = mk("f", "warn", kind="other"), mk("g", "retry", kind="other")
        # Pack default propagation through the namespaced branch: an
        # unknown LOCAL with a retry-default pack resolves retry (the
        # enforcement_for default leg — kindDefs → kinds → default).
        pe = PackManifest(
            namespace="e", name="e", version="0.1.0", tier="free",
            description="e", path=tmp_path,
            extraction={"enforcement": {
                "default": "retry", "kinds": {},
                "relations": {}, "chains": {},
            }},
        )
        # Relation + chain arm positives (the module's documented ladder —
        # relation: extraction.enforcement.relations → default; chain:
        # chain.enforcement → extraction.enforcement.chains → default).
        ph = PackManifest(
            namespace="h", name="h", version="0.1.0", tier="free",
            description="h", path=tmp_path,
            extraction={"enforcement": {
                "default": "warn", "kinds": {},
                "relations": {"grounds": "retry"}, "chains": {},
            }},
        )
        pi = PackManifest(
            namespace="i", name="i", version="0.1.0", tier="free",
            description="i", path=tmp_path,
            chains=[{"id": "c1", "steps": [], "enforcement": "block"}],
            extraction={"enforcement": {
                "default": "warn", "kinds": {},
                "relations": {}, "chains": {},
            }},
        )
        # kindDefs rung (the FIRST rung of the kind ladder): kindDefs wins
        # over extraction.enforcement.kinds.
        pj = PackManifest(
            namespace="j", name="j", version="0.1.0", tier="free",
            description="j", path=tmp_path,
            kind_defs={"widget": {"enforcement": "retry"}},
            extraction={"enforcement": {
                "default": "warn", "kinds": {"widget": "warn"},
                "relations": {}, "chains": {},
            }},
        )
        packs = {"a": pa, "b": pb, "c": pc, "d": pd, "e": pe,
                 "f": pf, "g": pg, "h": ph, "i": pi, "j": pj}
        # The stub must expose BOTH .packs (the pre-fix bare loop) AND
        # .get_pack (the fixed namespaced branch) — a plain dict would
        # AttributeError on the fixed code.
        stub = types.SimpleNamespace(packs=packs, get_pack=lambda ns: packs.get(ns))
        monkeypatch.setattr("tortoise.domain_loader._get_registry", lambda: stub)
        assert resolve_enforcement(kind="a:widget") == "retry"  # declaring pack wins
        # NOTE (review): on PRE-FIX code a:widget also resolves retry — pack
        # e's retry default participates in the bare max-scan for the full
        # namespaced string — so THIS line alone does not discriminate; the
        # discriminating pins are b:widget/c:widget/d:widget/e:/f:other.
        assert resolve_enforcement(kind="b:widget") == "warn"  # declaring pack's own default
        # NOTE: 'nuke' cannot reach the resolver via a LOADED pack (manifest
        # load validation rejects invalid enforcement levels) — this line
        # pins the resolve-time VALID_LEVELS guard as defense-in-depth for
        # programmatic/direct PackManifest construction only.
        assert resolve_enforcement(kind="c:widget") == "warn"  # invalid level → guard
        assert resolve_enforcement(kind="d:widget") == "block"  # valid-but-reserved, namespaced
        assert resolve_enforcement(kind="e:unknown-local") == "retry"  # pack default propagates
        assert resolve_enforcement(kind="e:") == "warn"  # EMPTY local hard-warns before pack lookup
        assert resolve_enforcement(kind="j:widget") == "retry"  # kindDefs rung beats kinds rung
        assert resolve_enforcement(kind=":rule") == "warn"  # EMPTY namespace → get_pack("") → None
        # BARE-kind contrast: the same stub also pins that bare kinds KEEP
        # the cross-pack max-severity scan (warn/retry/block across a/b/d;
        # invalid c is skipped by the guard) and that bare block resolves
        # with the max-scan early-exit — a regression scoping the bare path
        # to a single pack or dropping max-aggregation fails here.
        assert resolve_enforcement(kind="widget") == "block"
        # retry>warn ranking (warn declared first — a last-wins iteration
        # bug would return warn) + the BARE default leg (pack e's retry
        # default participates in the max-scan for an undeclared bare kind).
        assert resolve_enforcement(kind="other") == "retry"
        assert resolve_enforcement(kind="f:other") == "warn"  # declaring-pack exclusive
        assert resolve_enforcement(kind="g:other") == "retry"
        assert resolve_enforcement(kind="mystery") == "retry"  # bare → pack e's retry default

    def test_bare_max_severity_comparator(self, tmp_path, monkeypatch):
        """The bare cross-pack scan's max-severity COMPARATOR, isolated from
        default-leg leaks: pack x (retry) inserted BEFORE pack y (warn), all
        defaults warn — max-aggregation → retry, a last-wins iteration bug →
        warn. (The synthetic test's shared stub can't isolate this: pack e's
        retry default participates in every bare scan.)"""
        from tortoise.enforcement import resolve_enforcement
        from tortoise.pack_registry import PackManifest

        def mk(ns: str, level: str):
            return PackManifest(
                namespace=ns, name=ns, version="0.1.0", tier="free",
                description=ns, path=tmp_path,
                extraction={"enforcement": {
                    "default": "warn",
                    "kinds": {"comparator": level},
                    "relations": {}, "chains": {},
                }},
            )

        px, py = mk("x", "retry"), mk("y", "warn")
        packs = {"x": px, "y": py}
        stub = types.SimpleNamespace(packs=packs, get_pack=lambda ns: packs.get(ns))
        monkeypatch.setattr("tortoise.domain_loader._get_registry", lambda: stub)
        assert resolve_enforcement(kind="comparator") == "retry"  # max > last-wins
        assert resolve_enforcement(kind="x:comparator") == "retry"
        assert resolve_enforcement(kind="y:comparator") == "warn"

    def test_relation_chain_arms_resolve_positively(self, tmp_path, monkeypatch):
        """The seam's relation + chain arms (unchanged by #2030) resolve
        declared levels — the module's documented ladder (relation:
        extraction.enforcement.relations → default; chain: chain.enforcement
        → extraction.enforcement.chains → default), previously pinned only
        by warn fallbacks."""
        from tortoise.enforcement import resolve_enforcement
        from tortoise.pack_registry import PackManifest

        ph = PackManifest(
            namespace="h", name="h", version="0.1.0", tier="free",
            description="h", path=tmp_path,
            extraction={"enforcement": {
                "default": "warn", "kinds": {},
                "relations": {"grounds": "retry"}, "chains": {},
            }},
        )
        # Chain ladder MIDDLE rung: extraction.enforcement.chains[id] with
        # NO chain-level enforcement + a retry-DEFAULT pack resolving
        # undeclared relation/chain ids (the "→ default" legs of both arms).
        ph2 = PackManifest(
            namespace="h2", name="h2", version="0.1.0", tier="free",
            description="h2", path=tmp_path,
            chains=[{"id": "c2", "steps": []}],
            extraction={"enforcement": {
                "default": "retry", "kinds": {},
                "relations": {}, "chains": {"c2": "block"},
            }},
        )
        pi = PackManifest(
            namespace="i", name="i", version="0.1.0", tier="free",
            description="i", path=tmp_path,
            chains=[{"id": "c1", "steps": [], "enforcement": "block"}],
            extraction={"enforcement": {
                "default": "warn", "kinds": {},
                "relations": {}, "chains": {},
            }},
        )
        packs = {"h": ph, "h2": ph2, "i": pi}
        stub = types.SimpleNamespace(packs=packs, get_pack=lambda ns: packs.get(ns))
        monkeypatch.setattr("tortoise.domain_loader._get_registry", lambda: stub)
        assert resolve_enforcement(relation="grounds") == "retry"
        assert resolve_enforcement(relation="undeclared-rel") == "retry"  # h2 retry default
        assert resolve_enforcement(chain_id="c1") == "block"  # chain.enforcement rung
        assert resolve_enforcement(chain_id="c2") == "block"  # extraction.enforcement.chains rung
        assert resolve_enforcement(chain_id="undeclared-chain") == "retry"  # h2 retry default

    def test_ladder_precedence_beats_severity(self, tmp_path, monkeypatch):
        """The rung ORDER (not severity) is the single source of truth: an
        upper rung declaring LOWER severity than the pack default still
        wins (kindDefs → kinds → default; chain.enforcement →
        extraction.enforcement.chains → default). A per-pack max-aggregation
        refactor would fail here."""
        from tortoise.enforcement import resolve_enforcement
        from tortoise.pack_registry import PackManifest

        pk = PackManifest(
            namespace="k", name="k", version="0.1.0", tier="free",
            description="k", path=tmp_path,
            kind_defs={"widget": {"enforcement": "warn"}},
            chains=[{"id": "c1", "steps": [], "enforcement": "warn"}],
            extraction={"enforcement": {
                "default": "retry",
                "kinds": {"widget": "warn"},
                "relations": {"grounds": "warn"},
                "chains": {"c1": "block"},
            }},
        )
        packs = {"k": pk}
        stub = types.SimpleNamespace(packs=packs, get_pack=lambda ns: packs.get(ns))
        monkeypatch.setattr("tortoise.domain_loader._get_registry", lambda: stub)
        # kindDefs warn beats kinds warn beats default retry (rung order).
        assert resolve_enforcement(kind="k:widget") == "warn"
        # chain.enforcement warn (rung 1) beats extraction.enforcement.chains
        # block (rung 2) beats default retry — rung order, not severity.
        assert resolve_enforcement(chain_id="c1") == "warn"
        # relations warn beats default retry.
        assert resolve_enforcement(relation="grounds") == "warn"

    def test_registry_none_fails_open_to_warn(self, monkeypatch):
        """#2030 — the reg-is-None fail-open contract (previously only
        exercised with a real registry). NOTE: patch with a FUNCTION
        returning None — resolve_enforcement does a from-import of
        _get_registry at call time, so patching it to None directly would
        TypeError."""
        from tortoise.enforcement import resolve_enforcement
        monkeypatch.setattr("tortoise.domain_loader._get_registry", lambda: None)
        assert resolve_enforcement(kind="agent-ops:rule") == "warn"
        assert resolve_enforcement(kind="rule") == "warn"
        assert resolve_enforcement(relation="x") == "warn"
        assert resolve_enforcement(chain_id="x") == "warn"

    def test_only_agent_ops_rule_is_index_reachable_retry(self):
        """#2030 boundary-note-5 drift pin — compile the REAL kind index
        spec and assert exactly one index kind resolves retry. Pins the
        'product-strategy retry declarations stay dormant' claim (FIX P
        excludes point kinds from the index) against future manifest drift.
        RED on pre-fix code (every index kind resolves warn)."""
        from tortoise.enforcement import resolve_enforcement
        from tortoise.value_extractor import compile_kind_index_spec
        spec = compile_kind_index_spec()
        retry_kinds = {k for k in spec if resolve_enforcement(kind=k) == "retry"}
        assert retry_kinds == {"agent-ops:rule"}

    def test_warn_default(self):
        from tortoise.enforcement import resolve_enforcement
        assert resolve_enforcement(kind="no-such-kind-xyz") == "warn"
        assert resolve_enforcement(relation="no-such-rel") == "warn"
        assert resolve_enforcement(chain_id="no-such-chain") == "warn"
        # public-seam edges: no selector → warn (loop short-circuit); dual
        # selectors → kind arm wins (branch order).
        assert resolve_enforcement() == "warn"
        assert resolve_enforcement(kind="rule", relation="no-such-rel") == "retry"
        # relation/chain arms are bare-by-contract (#2030 kind-arm only):
        # colon-form relation/chain strings run the bare scan (warn here —
        # ambient assumption: no installed pack declares a non-warn default;
        # the block-reserved audit pins that assumption loudly).
        assert resolve_enforcement(relation="a:b:c") == "warn"
        assert resolve_enforcement(chain_id="a:b:c") == "warn"

    def test_block_reserved_but_resolvable(self):
        """block is a VALID_LEVELS member but RESERVED — no installed pack
        ships it today, asserted across the FULL declaration surface (kind
        default / kinds / kindDefs / relations / chains); block RESOLUTION
        through the seam is pinned in the synthetic stub (pack d) and the
        chain arm (chain c1 enforcement: block)."""
        from tortoise.domain_loader import _get_registry
        from tortoise.enforcement import VALID_LEVELS
        assert "block" in VALID_LEVELS
        reg = _get_registry()
        assert reg is not None, "block-reserved audit needs the real registry"
        for p in reg.packs.values():
            enc = p.extraction["enforcement"]
            levels = [enc.get("default")]
            levels += list(enc.get("kinds", {}).values())
            levels += list(enc.get("relations", {}).values())
            levels += list(enc.get("chains", {}).values())
            levels += [kd.get("enforcement") for kd in p.kind_defs.values()
                       if isinstance(kd, dict)]
            levels += [c.get("enforcement") for c in p.chains]
            assert all(lv != "block" for lv in levels if lv), \
                "no installed pack ships block today (reserved)"


# ── create_operator warn-not-block ─────────────────────────────────────────

class TestCreateOperatorWarnNotBlock:
    def test_undeclared_label_warns_but_writes(self, sdk):
        a, b = _two_points(sdk)
        result = sdk.create_operator("IMPL", a, [b], label="totallyUndeclaredVerb")
        assert "warnings" in result, "undeclared relation must return a warning"
        w = result["warnings"][0]
        assert w["code"] == "undeclared_relation"
        assert "totallyUndeclaredVerb" in w["message"]
        # warn-not-block: the write PROCEEDS — the operator node landed.
        assert result.get("id"), "the operator write must proceed (warn-not-block)"

    def test_declared_pack_relation_no_warning(self, sdk):
        """'addresses' is declared by product-strategy (feature→customerSegment
        etc.) — a declared predicate must NOT warn."""
        a, b = _two_points(sdk)
        result = sdk.create_operator("IMPL", a, [b], label="addresses")
        assert "warnings" not in result, "declared relation must not warn"

    def test_no_label_no_warning(self, sdk):
        a, b = _two_points(sdk)
        result = sdk.create_operator("IMPL", a, [b])
        assert "warnings" not in result
        assert result.get("id"), "the write must proceed"

    def test_violations_event_fires_on_warn_path(self, sdk, caplog):
        import ast
        import logging
        a, b = _two_points(sdk)
        with caplog.at_level(logging.WARNING, logger="tortoise.enforcement"):
            sdk.create_operator("IMPL", a, [b], label="bogusRel")
        # the violations event is a committed future data contract
        # ({event, code, kind|relation, pack, actor, ts}) — pin the
        # STRUCTURED shape, not just substrings. The transport
        # (log.warning("violation %s", event) → "violation <dict repr>") is
        # PART of the committed contract: a move to json.dumps is a
        # deliberate, reviewed format change (would require updating this
        # parse), not an accidental break.
        records = [r for r in caplog.records
                   if getattr(r, "name", "") == "tortoise.enforcement"]
        assert records, "violations event must be logged"
        parsed = [ast.literal_eval(r.getMessage().split(maxsplit=1)[1])
                  for r in records if r.getMessage().startswith("violation")]
        assert parsed and parsed[0]["event"] == "violation"
        assert parsed[0]["code"] == "undeclared_relation"
        assert parsed[0]["relation"] == "bogusRel"
        assert "ts" in parsed[0]


# ── Classifier near-miss retry signal ──────────────────────────────────────

# NOTE: the classifier-path seam resolution (namespaced agent-ops:rule →
# retry; near-miss partner core:standard → warn) is pinned in
# TestResolveEnforcement above + TestNearMissRetrySignal in
# tests/test_kind_classifier.py (which exercises the FULL hook with the
# real registry). A dedicated TestClassifierRetrySignal class duplicated
# those assertions verbatim and was removed (test-review cycle 7 — no
# discriminating value).

# ── Chain rewire regression guard (behavioral, not byte-identical) ─────────

class TestChainRewireUnchanged:
    def test_rewire_outcomes_stable_for_non_enforcement_pack(self):
        """The deterministic chain enforcer's graph-visible OUTCOMES for a
        non-enforcement scenario are stable — the regression guard for the
        enforcement wiring (behavioral equivalence, never byte-identical
        serialization assertions). A genuine reverse-chain pair
        (architecture → useCase in productDelivery order) is flagged and
        rewired through the nearest valid intermediate (feature)."""
        from tortoise.chain_enforcer import validate_and_rewire
        embed = {
            "entities": [
                {"name": "arch", "kind": "product-strategy:architecture",
                 "lifecycle": "created", "supersedes": None, "note": None},
                {"name": "useCase", "kind": "product-strategy:useCase",
                 "lifecycle": "created", "supersedes": None, "note": None},
                {"name": "feature", "kind": "product-strategy:feature",
                 "lifecycle": "created", "supersedes": None, "note": None},
            ],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [
                {"content": "arch connects to useCase",
                 "pointKind": "statement",
                 "about_entities": ["arch", "useCase"]},
            ],
        }
        _fixed, notes, stats = validate_and_rewire(embed)
        # A reverse-chain pair (architecture → useCase order) is detected
        # as a violation and deterministically rewired through the nearest
        # valid intermediate (feature) — the outcome is behavioral, never
        # byte-identical internals.
        assert notes, "reverse architecture→useCase must be flagged"
        assert notes[0]["action"] == "rewired"
        assert stats["violations"] == 1
        assert stats["rewired"] == 1
        assert stats["items_checked"] == len(embed["points"])
