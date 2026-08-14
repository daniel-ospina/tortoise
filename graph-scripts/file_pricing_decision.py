"""File the Pro + Team pricing decision to the Tortoise graph.

Run after verifying FalkorDB is up and TORTOISE_DB_URI is set:
  cd "$(dirname "$0")/.."
  python3 scripts/file_pricing_decision.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.projection import FalkorProjection

uri = os.environ.get("TORTOISE_DB_URI", "")
if not uri:
    print("ERROR: TORTOISE_DB_URI not set")
    sys.exit(1)

sdk = TortoiseSDK()
sdk._proj = FalkorProjection.from_uri(uri)

ctx = "licensing-decision"

# ── Criteria ──
criteria = {
    "criterion:competitor-positioning": "Price relative to Mem0, Zep, Letta, Supabase tiers",
    "criterion:conversion-rate": "Free-to-paid conversion rate sustainable at this price",
    "criterion:arpu-target": "Revenue per user sufficient for bootstrapped growth",
    "criterion:tier-separation": "Clear value gap between Community → Pro → Team",
    "criterion:market-signal": "Price signals quality without pricing out early adopters",
}

# ── Assumptions ──
assumptions = {
    "assumption:conversion-2pct": "Open source free→paid conversion at 2% baseline",
    "assumption:pro-solo": "Pro targets solo power users, not teams",
    "assumption:honor-system": "Self-host enforcement is honor-system; cloud is the premium",
    "assumption:funnel-builtin": "Library usage = built-in funnel; every user is a prospect",
}

# ── Options ──
options = {
    "option:pro-29": "Pro at $29/mo",
    "option:pro-49": "Pro at $49/mo",
    "option:pro-79": "Pro at $79/mo",
    "option:team-flat-99": "Team flat $99/mo unlimited",
    "option:team-flat-199": "Team flat $199/mo unlimited",
    "option:team-per-seat-99": "Team $99/mo up to 5 + $20/seat/mo",
}

# ── Research findings ──
findings = {
    "finding:devtool-sweetspot": "Dev tool Pro tiers: $29-$99 sweet spot, $49 common A/B test point (dev.to 2025)",
    "finding:oss-conversion": "Open source free→paid: 0.5-3% median, 2% benchmark (culta.ai, Lenny 2025)",
    "finding:freemium-benchmark": "Freemium median: 3%, top quartile: 5% (culta.ai 2026)",
    "finding:real-conversion-data": "$29 Pro: 1.2-4.3% conversion; $79 flat: 2.1% (dev.to case study 2026)",
    "finding:per-seat-nrr": "Per-seat NRR: 100-115%; feels fair, matches headcount budgeting (Forbes 2025)",
    "finding:flat-rate-leak": "Flat rate: revenue leak at high end, adoption friction at low end (softwarepricing.com 2025)",
    "finding:supabase-pro": "Supabase Pro $25: many production apps pay $35-75/mo actual (focusreactive 2026)",
    "finding:mem0-anchor": "Mem0: $19 Starter → $79 Growth → $249 Pro (Graph Memory gated at $249)",
}

# ── Decision ──
decision = {
    "decision:pro-49": "Pro tier set at $49/mo — solo power users, 10 graphs, 500K nodes, cloud, support",
    "decision:team-per-seat-99": "Team tier: $99/mo up to 5 seats + $20/seat/mo — shared graphs, RBAC, admin",
    "decision:pro-not-29": "Rejected $29: signals free tier isn't valuable, ARPU too low for OSS conversion rates",
    "decision:pro-not-79": "Rejected $79: direct Mem0 Growth competition, brand disadvantage",
    "decision:team-not-flat-99": "Rejected flat $99: only $50 above Pro, cannibalizes",
    "decision:team-not-flat-199": "Rejected flat $199: prices out 2-3 person startups, revenue leak at scale",
}

# ── Create all points ──
all_points = {**criteria, **assumptions, **options, **findings, **decision}
for pid, content in all_points.items():
    try:
        sdk.create_point(pid, content, context=ctx)
        print(f"  ✓ {pid}")
    except Exception as e:
        print(f"  ⚠ {pid}: {e}")

# ── Create operators (edges) ──
edges = [
    # Criteria → Options (IMPL)
    ("criterion:competitor-positioning", "IMPL", "option:pro-49"),
    ("criterion:conversion-rate", "IMPL", "option:pro-49"),
    ("criterion:arpu-target", "IMPL", "option:pro-49"),
    ("criterion:tier-separation", "IMPL", "option:pro-49"),
    ("criterion:market-signal", "IMPL", "option:pro-49"),
    ("criterion:competitor-positioning", "IMPL", "option:team-per-seat-99"),
    ("criterion:tier-separation", "IMPL", "option:team-per-seat-99"),
    ("criterion:arpu-target", "IMPL", "option:team-per-seat-99"),

    # Findings → Options (IMPL)
    ("finding:devtool-sweetspot", "IMPL", "option:pro-49"),
    ("finding:oss-conversion", "IMPL", "option:pro-49"),
    ("finding:real-conversion-data", "IMPL", "option:pro-49"),
    ("finding:supabase-pro", "IMPL", "option:pro-49"),
    ("finding:per-seat-nrr", "IMPL", "option:team-per-seat-99"),
    ("finding:flat-rate-leak", "IMPL", "option:team-per-seat-99"),

    # Findings → Rejected options (NAND)
    ("finding:oss-conversion", "NAND", "option:pro-29"),
    ("finding:mem0-anchor", "NAND", "option:pro-79"),
    ("finding:flat-rate-leak", "NAND", "option:team-flat-199"),
    ("finding:tier-separation-weak", "NAND", "option:team-flat-99"),

    # Assumptions → Options (IMPL)
    ("assumption:conversion-2pct", "IMPL", "option:pro-49"),
    ("assumption:pro-solo", "IMPL", "option:pro-49"),
    ("assumption:funnel-builtin", "IMPL", "option:pro-49"),

    # Decision → Supporting evidence (IMPL)
    ("finding:devtool-sweetspot", "IMPL", "decision:pro-49"),
    ("finding:oss-conversion", "IMPL", "decision:pro-49"),
    ("finding:per-seat-nrr", "IMPL", "decision:team-per-seat-99"),
    ("finding:flat-rate-leak", "IMPL", "decision:team-per-seat-99"),
]

for src, op_type, tgt in edges:
    try:
        sdk.create_operator(src, op_type, tgt)
        print(f"  ✓ {src} --{op_type}--> {tgt}")
    except Exception as e:
        print(f"  ⚠ {src} --{op_type}--> {tgt}: {e}")

# ── Compute confidence ──
try:
    # #395 (AC8): explicit max_hops=2 pin — no-arg compute_confidence is now
    # LOCAL EP over the dirty roots (exact closure). Pinning keeps the
    # decision artifact's persisted confidence values comparable across
    # runs on this fully-connected decision graph.
    sdk.compute_confidence(max_hops=2)
    print("\n✓ Confidence computed")
except Exception as e:
    print(f"\n⚠ compute_confidence: {e}")

# ── Verify structure ──
try:
    result = sdk.check_structure()
    print(f"✓ Structure check: {result}")
except Exception as e:
    print(f"⚠ check_structure: {e}")

print("\nDone. Decision filed to context='licensing-decision'")
