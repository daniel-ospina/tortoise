"""W3 Cat-34-style volunteering-memory harness (epic #2080, issue #2099, W3-a).

Hermetic real-seam replay harness: scripted-conversation fixtures replayed
through REAL integration seams (session_import write-back, SDK recall_state /
MCP tool seams, claude-hooks) on a throwaway hermetic graph, graded against
sealed gold (know-to-ask / false-fire / push precision-recall / write-back /
continuity / source-isolation suites).  This package is the fixture/gold/
baseline schema + grading + runner layer; the graded reflex decision layer
lands via the W4 delivery issue — an initial "no-reflex" baseline is honest
and publishable (the harness can fail per the fix-wave protocol).

Hermetic (pure validation + hashing, no DB/network/LLM) except the runner,
which opens its own throwaway graph per run.
"""
