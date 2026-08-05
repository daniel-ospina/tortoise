# ────────────────────────────────────────────────────────────────
# ARCHIVED (2026-08-05) — superseded by the generic `tortoise decide` command.
# This one-off data-filing script hardcoded the licensing decision content as
# Python literals and wrote it to the graph. It served as a pattern-prover for
# the generic decision-comparison tool (#43/#81) and as a manual re-load
# mechanism. Domain content belongs in the graph with Source provenance — do NOT
# use this script for new decisions; use `tortoise decide` instead.
#
# The licensing decision was re-filed to context 'licensing-decision-compare' on
# 2026-08-05 (recovery of the 2026-08-05 graph wipe). Kept for provenance/audit.
# ────────────────────────────────────────────────────────────────
"""File the licensing decision comparison to the Tortoise graph.

Compares the three license options recorded in graph research:
  1. AGPLv3 + commercial dual-licensing path (DEC-002, current)
  2. BSL 1.1 on EP engine + AGPLv3 on platform (Redis model)
  3. SSPL single license (MongoDB model)

Criteria + findings (from graph research) are wired to options via IMPL/NAND,
then EP belief propagation computes per-option confidence.

Run:
  cd "$(dirname "$0")/.."
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 graph-scripts/decide_licensing.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.projection import FalkorProjection

uri = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")

sdk = TortoiseSDK()
sdk._proj = FalkorProjection.from_uri(uri)

ctx = "licensing-decision-compare"

# ── Criteria (evaluation dimensions) ──
criteria = {
    "crit:protect-core": "Protect EP belief propagation (the core differentiator) from commercial exploitation by competitors",
    "crit:adoption": "OSI-approved / recognizable open source status for developer adoption and distribution",
    "crit:enterprise": "Enterprise procurement acceptability (legal clarity, no viral-license ban)",
    "crit:stack-compat": "Compatible with Apache 2.0 dependencies (FalkorDB, Graphiti)",
    "crit:flexibility": "Strategic flexibility — license is a choice, not a permanent commitment (exit ramp)",
    "crit:fork-risk": "Minimize hostile-fork / community-backlash risk (Redis→Valkey, Elastic→OpenSearch pattern)",
    "crit:revenue": "Dual-licensing revenue path (free self-host + paid commercial/enterprise license)",
}

# ── Options ──
options = {
    "opt:agplv3-dual": "AGPLv3 + dual-licensing path: AGPLv3 for free use (copyleft, network loophole closed), commercial license for enterprises that ban AGPL, CLA enables Apache 2.0 re-license (DEC-002)",
    "opt:bsl-ep-agpl": "BSL 1.1 on EP engine + AGPLv3 on platform: engine (belief propagation, IMPL/NAND, credibility, time decay) BSL-restricted, everything else (extraction, MCP, connectors, packs, SDK) AGPLv3 (Redis model)",
    "opt:sspl-single": "SSPL single license: free to use except offering as a service; offering as a service forces open-sourcing entire management stack (MongoDB model)",
}

# ── Findings (evidence from graph research) ──
findings = {
    "finding:agpl-network": "AGPLv3 closes the network loophole — competitors cannot offer closed-source SaaS without releasing modifications; strong copyleft for network services",
    "finding:agpl-osi": "AGPLv3 is OSI-approved — it IS open source, no 'open washing' accusations",
    "finding:agpl-market": "Market converging on AGPLv3 — Redis returned to AGPLv3 (May 2025), Elastic added it (Aug 2024)",
    "finding:agpl-compat": "AGPLv3 is Apache 2.0 compatible — works with Graphiti/FalkorDB deps",
    "finding:agpl-nocountdown": "AGPLv3 has no countdown — unlike BSL/FSL's 2-4 year forced conversion to permissive",
    "finding:agpl-revenue": "Dual-licensing proven: Grafana, MinIO, ScyllaDB generate $10K-$500K+/yr per enterprise for closed-source SaaS rights",
    "finding:agpl-enterprise-risk": "AGPL risk: enterprises ban AGPL (Google bans it internally); MongoDB moved FROM AGPL to SSPL because AGPL's 'viral' reputation scared enterprise procurement — risk is enterprise procurement, not developer adoption",
    "finding:bsl-protect": "BSL on EP means competitors cannot use belief propagation for any commercial purpose without a license — EP is the hardest thing to replicate",
    "finding:bsl-precedent": "BSL precedent: MongoDB, Elastic, Redis, CockroachDB, Sentry all use BSL or similar; enterprises prefer BSL over AGPL for legal clarity",
    "finding:bsl-converts": "BSL converts to Apache 2.0 after 4 years — community contributors accept it because code eventually becomes fully open",
    "finding:bsl-osi-gap": "OSI does not recognize BSL — source-available, not OSI-certified open source; 'open washing' accusations possible",
    "finding:bsl-fork-risk": "BSL/operations-only gating triggered hostile forks when paired with no value-add: Redis→Valkey (2024), Elastic→OpenSearch (2021), HashiCorp→OpenTofu (2023)",
    "finding:sspl-force": "SSPL is more aggressive than BSL: forces competitors who offer a service to open-source their ENTIRE management stack (orchestration, monitoring, backup, user management)",
    "finding:sspl-blocked": "SSPL risk: OSI rejected SSPL as 'not open source'; Debian/Fedora/Red Hat removed MongoDB; Linux distros cannot package SSPL software — blocks distribution channels",
    "finding:sspl-adoption": "MongoDB survived SSPL only because it had MASSIVE adoption before the change (2018, 9 years post-launch); Tortoise has zero adoption — applying SSPL now blocks distribution before any adoption exists",
    "finding:ep-math": "EP propagation is algorithmic (Beta math), not a trained model — competitors can implement belief propagation; gating EP gates math, not intelligence (weak moat argument)",
    "finding:ep-moat-strong": "Counter: getting EP right on loopy graphs with credibility tiers (bias/precision/consistency/directness), IMPL/NAND semantics, time decay is correctness-critical — no competitor (Mem0, Graphiti, Cognee, Supermemory) has anything comparable",
    "finding:cla-exit": "AGPLv3 + CLA enables re-license to Apache 2.0 at any time — 'the license is a strategic choice, not a permanent commitment' (stress-response-license)",
    "finding:competitors-mit": "Almost all competitors are MIT (Paperclip, LangGraph, Shepherd, ESAA, transitions, pre-commit) — they're VC-funded or community projects that don't need license revenue; Tortoise's bootstrapped position justifies a different license",
    "finding:redis-split": "Redis dual-license precedent: BSD core (drives adoption) + RSAL modules (revenue protection) — the proven pattern for core-permissive + advanced-features-restricted",
    "finding:transition": "Transition plan (DEC-002): NOW do nothing (internal), before first public release add AGPLv3 LICENSE + SPDX + CLA, at first paying customer publish commercial terms — protects 'internal infra → possible product' without premature decision",
}

# ── Decision mapping: findings/criteria → options (IMPL support, NAND oppose) ──
edges = [
    # Criteria → options
    ("crit:protect-core", "IMPL", "opt:bsl-ep-agpl"),
    ("crit:protect-core", "IMPL", "opt:sspl-single"),
    ("crit:adoption", "IMPL", "opt:agplv3-dual"),
    ("crit:adoption", "NAND", "opt:bsl-ep-agpl"),
    ("crit:enterprise", "IMPL", "opt:bsl-ep-agpl"),
    ("crit:enterprise", "NAND", "opt:agplv3-dual"),
    ("crit:stack-compat", "IMPL", "opt:agplv3-dual"),
    ("crit:stack-compat", "IMPL", "opt:bsl-ep-agpl"),
    ("crit:flexibility", "IMPL", "opt:agplv3-dual"),
    ("crit:flexibility", "NAND", "opt:bsl-ep-agpl"),
    ("crit:fork-risk", "IMPL", "opt:agplv3-dual"),
    ("crit:fork-risk", "NAND", "opt:bsl-ep-agpl"),
    ("crit:fork-risk", "NAND", "opt:sspl-single"),
    ("crit:revenue", "IMPL", "opt:agplv3-dual"),
    ("crit:revenue", "IMPL", "opt:bsl-ep-agpl"),

    # Findings → AGPLv3 dual
    ("finding:agpl-network", "IMPL", "opt:agplv3-dual"),
    ("finding:agpl-osi", "IMPL", "opt:agplv3-dual"),
    ("finding:agpl-market", "IMPL", "opt:agplv3-dual"),
    ("finding:agpl-compat", "IMPL", "opt:agplv3-dual"),
    ("finding:agpl-nocountdown", "IMPL", "opt:agplv3-dual"),
    ("finding:agpl-revenue", "IMPL", "opt:agplv3-dual"),
    ("finding:agpl-enterprise-risk", "NAND", "opt:agplv3-dual"),
    ("finding:cla-exit", "IMPL", "opt:agplv3-dual"),
    ("finding:transition", "IMPL", "opt:agplv3-dual"),
    ("finding:competitors-mit", "IMPL", "opt:agplv3-dual"),

    # Findings → BSL+AGPL
    ("finding:bsl-protect", "IMPL", "opt:bsl-ep-agpl"),
    ("finding:bsl-precedent", "IMPL", "opt:bsl-ep-agpl"),
    ("finding:bsl-converts", "IMPL", "opt:bsl-ep-agpl"),
    ("finding:bsl-osi-gap", "NAND", "opt:bsl-ep-agpl"),
    ("finding:bsl-fork-risk", "NAND", "opt:bsl-ep-agpl"),
    ("finding:redis-split", "IMPL", "opt:bsl-ep-agpl"),
    ("finding:ep-math", "NAND", "opt:bsl-ep-agpl"),
    ("finding:ep-moat-strong", "IMPL", "opt:bsl-ep-agpl"),
    ("finding:agpl-compat", "IMPL", "opt:bsl-ep-agpl"),
    ("finding:agpl-revenue", "IMPL", "opt:bsl-ep-agpl"),

    # Findings → SSPL
    ("finding:sspl-force", "IMPL", "opt:sspl-single"),
    ("finding:sspl-blocked", "NAND", "opt:sspl-single"),
    ("finding:sspl-adoption", "NAND", "opt:sspl-single"),
    ("finding:bsl-protect", "IMPL", "opt:sspl-single"),
    ("finding:ep-moat-strong", "IMPL", "opt:sspl-single"),
]

# ── Create all points ──
all_points = {**criteria, **options, **findings}
point_ids = {}
for pid, content in all_points.items():
    try:
        kind = "criterion" if pid.startswith("crit:") else ("option" if pid.startswith("opt:") else "evidence")
        p = sdk.create_point(kind, content, context=ctx, status="live")
        point_ids[pid] = p["id"]
        print(f"  ✓ {pid} → {p['id']}")
    except Exception as e:
        print(f"  ⚠ {pid}: {e}")

# ── Create operators (edges) ──
for src, op_type, tgt in edges:
    try:
        sdk.create_operator(op_type, point_ids[src], [point_ids[tgt]], context=ctx)
        print(f"  ✓ {src} --{op_type}--> {tgt}")
    except Exception as e:
        print(f"  ⚠ {src} --{op_type}--> {tgt}: {e}")

# ── Compute confidence per option ──
try:
    result = sdk.compute_confidence(context=ctx)
    print(f"\n✓ EP computed: {result['iterations']} iterations, converged={result['converged']}")
    confs = result.get("confidences", {})
    opt_conf = {}
    for pid, cid in point_ids.items():
        if pid.startswith("opt:"):
            opt_conf[pid] = confs.get(cid, {}).get("mean", "n/a")
    print("\n=== OPTION CONFIDENCE (higher = more supported) ===")
    ranked = sorted(opt_conf.items(), key=lambda kv: (kv[1] if isinstance(kv[1], float) else 0), reverse=True)
    for pid, c in ranked:
        print(f"  {pid}: {c:.4f}" if isinstance(c, float) else f"  {pid}: {c}")
except Exception as e:
    print(f"\n⚠ compute_confidence: {e}")

# ── Verify structure ──
try:
    result = sdk.check_structure()
    issues = [i for i in result if "licensing" in str(i.get("id", "")) or "licensing" in str(i.get("message", ""))]
    print(f"\n✓ Structure check: {len(result)} issues total, {len(issues)} licensing-related")
except Exception as e:
    print(f"⚠ check_structure: {e}")

print(f"\nDone. Decision comparison filed to context='{ctx}'")
