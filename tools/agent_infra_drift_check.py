#!/usr/bin/env python3
"""CI entry point for the agent-infra drift gate: materialise ``scripts``, then run ``check``.

Why a wrapper and not an inline ``check-command`` script
--------------------------------------------------------
``.github/workflows/ci.yml``'s ``drift-check`` job calls agent-infra's reusable
``drift-check.yml`` with its own ``check-command`` override (the override materialises the
machine-local ``scripts`` pointer first — see the comment there for why). The reusable
workflow consumes that input by **interpolating it verbatim inside a double-quoted
assignment** (``cmd="${{ inputs.check-command }}"`` … ``bash -c "$cmd"``), and ``${{ }}``
substitution is TEXTUAL with no escaping. So ANY ``"``, ``(``, ``)``, backtick or ``${{``
in the override's body terminates the assignment early and leaves the remainder parsed as
shell: the step dies with ``syntax error near unexpected token '('`` at PARSE time, before
it can record a result, and the gate reports "the drift check produced NO result" forever —
a red that names a setup fault while removing the whole gate's coverage (#9541 review,
round 6, P1).

A single quote-free command survives the interpolation byte-for-byte. This script is that
command; everything the override used to do inline lives here, where quoting is python's
problem rather than the workflow consumer's:

    check-command: python3 tools/agent_infra_drift_check.py

Contract
--------
1. Materialise ``scripts`` from the agent-infra checkout (``tools/link_agent_infra_scripts.py``),
   quietly — the link tool owns that logic and its exit contract (0 ok, 1 wrong pointer,
   2 config error).
2. Run the agent-infra checker UNCHANGED: ``node <infra>/bin/agent-infra.js check . --ci``,
   which is the reusable workflow's own default invocation. The gate still verifies the
   pointer (and every other surface); nothing is bypassed or exempted.
3. FAIL CLOSED. The checker runs even when materialisation failed, so a broken setup still
   produces the checker's own diagnosis instead of a silent skip. Non-zero from the checker
   is its verdict and is returned as-is; a clean checker over a pointer this tool could not
   write is exit 2, never 0 — a gate must not read green over a link it knows is wrong.

Exit codes
----------
    N  ``node … check . --ci``'s own exit code, when non-zero (the checker's verdict)
    2  config error: no agent-infra checkout, no ``node``, or the pointer could not be
       materialised while the checker still passed
    0  the pointer is materialised AND the checker passed
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from link_agent_infra_scripts import (
    ConfigError,
    resolve_infra,
)
from link_agent_infra_scripts import main as link_main

#: agent-infra's CLI, relative to the checkout root — the path the reusable workflow's own
#: default ``check-command`` uses. Kept here as a constant so a drift in that default is a
#: one-line change, with the pinning test in tests/test_link_agent_infra_scripts.py.
CHECKER = Path("bin") / "agent-infra.js"
CHECK_ARGS = ("check", ".", "--ci")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Materialise the machine-local `scripts` pointer, then run agent-infra's drift "
            "check (#9475). The single quote-free command ci.yml's drift-check passes as "
            "`check-command`."
        )
    )
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent),
                        help="repo root (default: the checkout holding this script)")
    parser.add_argument("--infra", default=None,
                        help="agent-infra checkout (default: $AGENT_INFRA_PATH); authoritative when passed")
    parser.add_argument("--node", default=None,
                        help="node executable (default: `node` on PATH, or $AGENT_INFRA_NODE)")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    node = args.node or os.environ.get("AGENT_INFRA_NODE") or "node"

    # 1. Materialise. Its stderr already explains a refusal, so pass its exit through as a
    #    FACT to weigh after the checker runs — do not turn it into an early return, or a
    #    broken setup would skip the check entirely (the "no result" red is the defect this
    #    wrapper exists to remove).
    link_rc = link_main(
        ["--quiet", "--repo", str(repo)] + (["--infra", args.infra] if args.infra else [])
    )

    try:
        infra = resolve_infra(args.infra)
    except ConfigError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2

    command = [node, str(infra / CHECKER), *CHECK_ARGS]
    try:
        checked = subprocess.run(command, cwd=str(repo))
    except FileNotFoundError:
        print(
            f"⛔ {node!r} is not executable, so agent-infra's drift check cannot run. "
            f"Intended: {' '.join(command)} (repo {repo}).",
            file=sys.stderr,
        )
        return 2

    if checked.returncode != 0:
        if link_rc != 0:
            print(
                f"⛔ the `scripts` pointer could not be materialised (exit {link_rc}), AND "
                "agent-infra's drift check failed. The failure above may name the pointer "
                "rather than real drift: run `uv run python tools/link_agent_infra_scripts.py` "
                "to see why the pointer was refused.",
                file=sys.stderr,
            )
        return checked.returncode

    if link_rc != 0:
        # The checker passed, so it does not test the pointer — but this job DOES gate it,
        # and reporting success over a pointer we failed to write is the one thing the
        # #9475 fix must never do (PR #9541 review).
        print(
            f"⛔ agent-infra's drift check passed, but the machine-local `scripts` pointer "
            f"could NOT be materialised (exit {link_rc}) — see above. Refusing to report "
            "green over a pointer the drift gate exists to verify.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
