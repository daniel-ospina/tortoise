"""W3-b why-layer suite (epic #2080, issue #2100) — planted-conflict gold
conventions + A11 surfaced-context grading.

The Tortoise-original why-layer eval: a planted-conflict corpus (shared with
W4-a's E2E-1 seeding — 40 fictional points: 30 conflicted [10 P9 / 5
decision / 5 superseded subsets] + 10 clean controls) is seeded onto a
hermetic graph, the W4 why-block assembly (``tortoise.why`` —
``assemble_why_blocks``) produces the canonical §3.1.4 surfaced context per
point, and the suite grades the four why-questions (what contradicts this /
why is this believed / which alternative does EP favor / where do I dig
deeper) from the surfaced context ALONE (A11 — no graph access beyond it).

This package mirrors the W3-a volunteering-memory harness (tests/eval/
harness/) conventions exactly: sealed gold separate from the harness-visible
manifest (a ``gold`` key in the manifest = validation error), ``fixtures_hash``
over manifest + gold, jointly-pinned corpus manifest (the deterministic
seed → planted composition shared with W4-a's E2E-1 seeding), BPRE-style
posture-scoped baselines (``baselines/main.json`` + ``baselines/m2.json``),
receipts with validated per-run rows, and the PINNED ``judge_why_suite_v1``
(prompt hash recorded in the baseline ``judge_pin``; asserted in the grading
pre-step — a judge/gold change is a protocol change, never a silent compare).

Hermetic: pure validation/hashing modules import no DB/network/LLM.  The
runner opens its own throwaway hermetic graph per run (seeding + assembly +
grading are deterministic and zero-LLM — the pinned rubric is implemented
mechanically; no provider key is ever required).
"""
