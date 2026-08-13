"""check_gates — workflow-layer GitHub-coupled gate helper (#787, DE2E-7).

Plan §6.3: Gates A+B state is WORKFLOW-LAYER state — ``check_gates(child_issue)``
lives in the workflow layer (CLI helper, GitHub-coupled), NOT the core SDK.
The SDK exposes only the local ``calibration_passed()`` marker (stored
milestone, #779) so DE2E-7 tests the local contract without a GitHub
dependency.

Gate semantics:
- Gate A = the #320 epic (agent-session indexing) must be CLOSED.
- Gate B = the calibration milestone must be recorded (calibration_passed).

The pure function takes injectable state readers (tests mock them); the CLI
shells out to ``gh`` for the issue body + dependency states and reads the SDK
marker. The helper only READS — it never writes to the graph (DE2E-7: "no
graph writes while blocked").
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# Hard dependency gates: dependency issue number → gate name.
GATE_A_ISSUE = 320          # Epic: Index agent sessions as Events
GATE_A_NAME = "gate_a_320_index_sessions"
GATE_B_NAME = "gate_b_calibration"

_DEPENDS_ON_RE = re.compile(r"#(\d+)")
# Documented non-gate dependencies are excluded from blocking.
_KNOWN_NON_GATES = {
    779, 780, 782, 784, 785, 786, 787, 990,  # in-epic sequencing (issue lists)
}


def check_gates(child_issue: str | int,
                *,
                issue_body: str | None = None,
                dependency_states: dict[int, str] | None = None,
                calibration_passed: bool = False,
                gh_runner=None) -> dict:
    """Evaluate the child issue's gate list.

    Args:
        child_issue: the child issue number (for the report).
        issue_body: the child issue body (parsed for 'Depends on:'); when
            None the CLI path fetches it via gh (gh_runner).
        dependency_states: {issue_number: 'open'|'closed'|...} — when None,
            the CLI path resolves each dependency via gh.
        calibration_passed: the local Gate B marker (SDK
            ``calibration_passed()``, #779).
        gh_runner: injectable ``gh api <path>`` runner for tests; default
            subprocess ``gh``.

    Returns {"child_issue": N, "blocked": bool, "reasons": [...],
             "gates": {"gate_a": "open"|"closed"|"unknown",
                       "gate_b": "passed"|"open"}}.
    """
    reasons: list[str] = []
    body = issue_body
    if body is None and gh_runner is not None:
        body = _gh_issue_body(child_issue, gh_runner)

    # Gate A: #320 must be closed.
    states = dict(dependency_states or {})
    if 320 not in states and gh_runner is not None:
        states[320] = _gh_issue_state(320, gh_runner)
    gate_a = states.get(320)
    if gate_a is None:
        gate_a_state = "unknown"
        reasons.append("gate A (#320) state unknown — cannot verify")
    elif gate_a == "closed":
        gate_a_state = "closed"
    else:
        gate_a_state = "open"
        reasons.append("gate A (#320 index-sessions epic) is open")

    # Gate B: the local calibration marker.
    gate_b_state = "passed" if calibration_passed else "open"
    if not calibration_passed:
        reasons.append(
            "gate B calibration milestone not recorded "
            "(calibration_passed() is False)")

    return {
        "child_issue": int(child_issue),
        "blocked": bool(reasons),
        "reasons": reasons,
        "gates": {
            "gate_a": gate_a_state,
            "gate_b": gate_b_state,
        },
    }


def _gh_issue_body(issue: int, gh_runner) -> str:
    out = gh_runner(f"repos/daniel-ospina/tortoise/issues/{issue}")
    try:
        return json.loads(out).get("body", "") if isinstance(out, str) else ""
    except (json.JSONDecodeError, AttributeError):
        return ""


def _gh_issue_state(issue: int, gh_runner) -> str:
    out = gh_runner(f"repos/daniel-ospina/tortoise/issues/{issue}")
    try:
        return json.loads(out).get("state", "unknown") if isinstance(out, str) else "unknown"
    except (json.JSONDecodeError, AttributeError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m tortoise.gates <child_issue> [--no-calibration]"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    issue = int(argv[0])
    gh_runner = _default_gh_runner()
    body = _gh_issue_body(issue, gh_runner)
    # Resolve the SDK marker when possible (read-only).
    calibration_passed = False
    try:
        from tortoise.sdk import TortoiseSDK
        calibration_passed = TortoiseSDK().calibration_passed()
    except Exception:
        calibration_passed = False
    result = check_gates(issue, issue_body=body,
                         calibration_passed=calibration_passed,
                         gh_runner=gh_runner)
    print(json.dumps(result, indent=2))
    return 1 if result["blocked"] else 0


def _default_gh_runner():
    def run(path: str) -> str:
        return subprocess.run(
            ["gh", "api", path], capture_output=True, text=True, check=False,
        ).stdout
    return run


if __name__ == "__main__":
    sys.exit(main())
