"""Battery harness core (epic #1402, issue #1406).

Internal tooling for the Agent-Reasoning Eval Battery: an episode runner
(trajectory logging, seed pinning, model-call outcome tracking, batch
scenario setup), a CLI (run|parity|calibrate|validate-judge|report + exit
codes 0/1/2/3/4/5), and the config/ YAML loaders — extending
tools/longmem_eval patterns, contract-first for child issues (#1408 arms,
#1410 judge gate, #1414 parity, #1415 calibrate/report).

See docs/plans/2026-08-17-1406-battery-harness-core.md (plan) and
docs/plans/2026-08-17-1406-scope.md (scope, converged contract).
"""

__version__ = "0.1.0"
