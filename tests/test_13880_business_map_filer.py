"""#13880 — the business-map filer must actually FILE, not just print a plan.

Measured on PR #13913 before this file existed: `--dry-run` printed a clean
green light (`validate()` errors = []) and `--write` could not write a single
node. `_resolve` was defined INSIDE `write_map` as
`def _resolve(self, item_ref, name, res, what)` and called at five sites with
four positional arguments, so the first node raised

    TypeError: write_map.<locals>._resolve() missing 1 required positional
    argument: 'what'

before anything was sent. A nested function has no `self` to receive, and no
`--dry-run` can see it: the validator never enters the writer. That is the same
class of false PASS the filer itself was fixed for (#13880's `isError` repair) —
a green light over a path that cannot execute.

WHY THE ASSERTION IS A REAL CALL AND NOT A SIGNATURE CHECK. A test that
inspects `_resolve`'s parameters would pass on a `functools.partial` or a
`staticmethod` spelling that still cannot be CALLED, and it would not notice a
future edit that moves the argument order. So the stub transport below is a
functioning transport, `write_map` runs end to end over a five-node document,
and the assertions are on what the transport was ASKED to do.

THE MUTANT. Restore `self` as `_resolve`'s first parameter — the one-token edit
that reverts this fix — and `test_write_map_ranks_ids_from_the_transport` goes
red with the TypeError above. Restore the old name-fallback behaviour (return
the item's name when the response carries no id) and
`test_write_map_refuses_an_item_whose_write_returned_no_id` goes red instead,
because the run would complete with plausible-looking stand-in ids.

AND THE VERIFIER, which had the same defect one level up: `--verify` compared a
whole-graph node count against this map's size with a 10% tolerance, so a run
that wrote 71 of 78 nodes exited 0, and the edge count was printed but never
asserted (a graph of all nodes and no edges answers no question).
`verify_problems` is that verdict, pure and exact; restoring the ratio
(`len(missing) > len(written) * 0.1`) reds the first test below.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import ClassVar

import pytest

_FILER = (Path(__file__).resolve().parent.parent
          / "graph-scripts" / "business_map_13880" / "file_map.py")
_spec = importlib.util.spec_from_file_location("business_map_13880_filer", _FILER)
assert _spec and _spec.loader
filer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(filer)


class _StubMcp:
    """A transport that answers every write with a fresh id, and records the call.

    It is deliberately NOT a `Mock`: the assertion is on the args the writer
    built, so a call recorded here is exactly what would have gone on the wire.
    """

    instances: ClassVar[list[_StubMcp]] = []

    def __init__(self, key: str, timeout: float = 120.0):
        self.key = key
        self.timeout = timeout
        self.session = None
        self.calls: list[tuple[str, dict]] = []
        self._n = 0
        _StubMcp.instances.append(self)

    def initialize(self) -> None:
        self.session = "stub-session"

    def call(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        self._n += 1
        return {"structuredContent": {"id": f"n{self._n}"}}


class _NoIdMcp(_StubMcp):
    """Answers every write with a payload carrying no id at all."""

    def call(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        return {"structuredContent": {"ok": True, "created": "maybe"}}


def _doc() -> dict:
    """Five nodes, one operator edge and one structural edge — the smallest map
    that reaches every `_resolve` call site except the fourth (events), which
    the event item below adds."""
    return {
        "subjects": [{"ref": "s1", "name": "Premise Labs", "subjectKind": "organization"}],
        "objects": [{"ref": "o1", "name": "The business map", "objectKind": "Project"}],
        "sources": [{"ref": "src1", "url": "https://example.test/map",
                     "sourceKind": "document"}],
        "events": [{"ref": "ev1", "eventKind": "decision", "content": "the map is judged"}],
        "points": [{"ref": "p1", "content": "the map is filed", "kind": "statement",
                    "source": "src1"}],
        "edges": [
            {"from": "p1", "to": "o1", "op": "IMPL", "label": "supports", "direction": "->"},
            {"from": "o1", "to": "s1", "relation": "ownedBy"},
        ],
    }


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Point the filer at a stub transport and a throwaway state file."""
    monkeypatch.setenv("TORTOISE_MCP_API_KEY", "stub-key")
    monkeypatch.setattr(filer, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(filer, "Mcp", _StubMcp)
    _StubMcp.instances.clear()
    return _StubMcp


def test_write_map_ranks_ids_from_the_transport(wire):
    """Every node is bound to the id the transport RETURNED, never to its name."""
    assert filer.write_map(_doc()) == 0

    mcp = _StubMcp.instances[-1]
    created = [tool for tool, _ in mcp.calls]
    assert created == [
        "tortoise_create_subject",
        "tortoise_create_object",
        "tortoise_create_source",
        "tortoise_create_event",
        "tortoise_create_point",
        "tortoise_create_operator",
        "tortoise_create_edge",
    ]

    # The edge endpoints are the transport's ids, not the map's refs and not a
    # name fallback: this is the assertion the TypeError mutant reddens, and the
    # one a "return the name" regression reddens too.
    op_args = mcp.calls[5][1]
    edge_args = mcp.calls[6][1]
    assert op_args["src"] == "n5", op_args          # point p1
    assert op_args["targets"] == ["n2"], op_args    # object o1
    assert edge_args["from_id"] == "n2", edge_args  # object o1
    assert edge_args["to_id"] == "n1", edge_args    # subject s1

    state = filer.load_state()
    assert state["ids"] == {"s1": "n1", "o1": "n2", "src1": "n3",
                            "ev1": "n4", "p1": "n5"}
    assert state["failed"] == []
    assert len(state["written"]) == 7


def test_write_map_refuses_an_item_whose_write_returned_no_id(wire, monkeypatch):
    """A payload with no id STOPS the run and names the item — it never falls
    back to the item's own name, which is how a degraded API once "filed" every
    node to a name that was never an id."""
    monkeypatch.setattr(filer, "Mcp", _NoIdMcp)
    _NoIdMcp.instances.clear()

    with pytest.raises(RuntimeError) as excinfo:
        filer.write_map(_doc())

    msg = str(excinfo.value)
    assert "subject" in msg and "s1" in msg, msg
    assert "returned no id" in msg, msg
    # It stopped at the FIRST write: nothing after the refusal was attempted.
    assert [t for t, _ in _NoIdMcp.instances[-1].calls] == ["tortoise_create_subject"]


def test_verify_refuses_a_partial_filing():
    """One missing node out of 78 is ONE MISSING NODE, not a 98%-complete pass.

    The ratio mutant (`len(missing) > len(written) * 0.1`) leaves this green,
    which is exactly what the old `got >= expected * 0.9` did to a 71-of-78
    filing.
    """
    written = [f"n{i}" for i in range(1, 79)]
    assert filer.verify_problems(written, set(written), 101, 101) == []

    lost = set(written) - {"n7"}
    problems = filer.verify_problems(written, lost, 101, 101)
    assert len(problems) == 1, problems
    assert "1 of 78" in problems[0], problems[0]
    assert "n7" in problems[0], problems[0]


def test_verify_refuses_an_edge_shortfall():
    """Nodes without edges are a filed-looking map that answers no question."""
    written = ["n1", "n2"]
    problems = filer.verify_problems(written, set(written), 100, 101)
    assert len(problems) == 1, problems
    assert "100 edge" in problems[0] and "expected 101" in problems[0], problems[0]
    assert "1 not filed" in problems[0], problems[0]

    # Both halves can fail at once, and both are then reported.
    both = filer.verify_problems(written, {"n1"}, 0, 101)
    assert len(both) == 2, both
