"""#10084 before/after at the rebased head.

BEFORE (pre-#10504 tree): a graph-free write resolves to the workspace default
graph and the result says NOTHING about it -> the caller cannot tell where the
data went. Proven structurally: the seam does not exist in that tree.

AFTER (head 76b03606a): the same write discloses the resolved target.
"""
from __future__ import annotations

import sys

from tortoise import mcp_auth
from tortoise import mcp_server as ms

ORG = "org_demo"


def show(label: str, info) -> None:
    print(f"--- {label}")
    if info is None:
        print("    disclosure: <NONE>  -> the caller is told nothing about the target")
        return
    print(f"    disclosure keys: {sorted(info)}")
    print(f"    message: {ms._graph_disclosure_message(info)}")


def main() -> int:
    # A hosted WORKSPACE credential: org-wide, no graph binding.
    mcp_auth._current_org_id.set(ORG)
    mcp_auth._current_graph_id.set(None)

    show("write that names NO graph (workspace credential)",
         ms._graph_disclosure(None))

    show('write that names graph="default" (the resolver no-op)',
         ms._graph_disclosure("default"))

    show("write that NAMES a graph (caller-named, reported as given)",
         ms._graph_disclosure("Fund A / Research"))

    # A graph-BOUND key: omission opens that key's OWN graph, not the default.
    mcp_auth._current_graph_id.set("g_fund_a")
    show("write that names NO graph (graph-bound key -> its own graph)",
         ms._graph_disclosure(None))

    # A write that touches no graph at all must NOT be disclosed.
    mcp_auth._current_org_id.set(None)
    mcp_auth._current_graph_id.set(None)
    show("stdio / self-host, no hosted workspace", ms._graph_disclosure(None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
