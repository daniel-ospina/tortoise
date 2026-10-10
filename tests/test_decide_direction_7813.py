"""#7813 — the decision-authoring CLI must be able to express a direction.

The `decide` command wired every operator through `create_operator`'s default and
could not say "this is a directed attack", so NANDs meant as *"X attacks Y"* were
silently stored as **mutual exclusion**. `--direction` is now threaded to all
three `create_operator` call sites (`edges`, `truth_edges`, `relevance_edges`).

These tests prove:

1. the CLI accepts a direction and the chosen value reaches **all three** call
   sites (`test_direction_reaches_all_three_call_sites`);
2. the unset default still stores `"bidirectional"` — i.e. #807/#753/#86 were not
   reversed (`test_default_omits_direction_kwarg` + the live
   `test_default_stores_bidirectional_live`);
3. accepted values are not widened — only the two `create_operator` values are
   legal (`test_direction_choices_stay_sdk_bounded`; widening is #7852).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils
from tortoise import __main__ as m

# ── Unit lane: a fake SDK records the call sites; no graph needed ───────────


class _FakeProj:
    def close(self) -> None:
        pass


class _FakeSDK:
    """Record every `create_operator` call so the test can inspect what the
    CLI threaded to each of the three wiring loops."""

    instances: ClassVar[list[_FakeSDK]] = []

    def __init__(self) -> None:
        self._proj = None
        self.operator_calls: list[dict] = []
        _FakeSDK.instances.append(self)

    def create_point(self, kind, content, dedup=True, **kwargs):
        return {"id": f"pt:{content}"}

    def create_operator(self, op_type, source_id, target_ids, label=None, **kwargs):
        self.operator_calls.append(
            {
                "op_type": op_type,
                "source": source_id,
                "targets": list(target_ids),
                "label": label,
                "kwargs": kwargs,
            }
        )
        return {"id": f"op:{len(self.operator_calls)}"}

    def mitigate_operator(self, op_id, reason, strength):
        return {"id": "mit:1"}

    def compute_confidence(self, factors=None):
        return {"iterations": 0, "converged": True, "confidences": {}}


_DECISION = {
    "options": {"opt:a": "Option A", "opt:b": "Option B"},
    "criteria": {"crit:1": "Criterion 1"},
    "findings": {"finding:1": "F1", "finding:2": "F2", "finding:3": "F3"},
    # one IMPL + one NAND in the regular `edges` loop
    "edges": [["crit:1", "IMPL", "opt:a"], ["crit:1", "NAND", "opt:b"]],
    # the `truth_edges` loop
    "truth_edges": [{"source": "finding:1", "op_type": "NAND", "target": "finding:2"}],
    # the `relevance_edges` loop — a *distinct* (src, op_type, tgt) so it does
    # not reuse an operator from `edges` and its create_operator call is made
    "relevance_edges": [
        {
            "source": "finding:3",
            "op_type": "NAND",
            "target": "opt:a",
            "reason": "overstated",
            "strength": 0.2,
        }
    ],
}


def _write_input(tmp_path: Path) -> str:
    path = tmp_path / "decision.json"
    path.write_text(json.dumps(_DECISION), encoding="utf-8")
    return str(path)


@pytest.fixture()
def fake_sdk(monkeypatch):
    _FakeSDK.instances.clear()
    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    monkeypatch.setattr(m, "_projection_for", lambda target: _FakeProj())
    monkeypatch.setattr(m, "_resolve_db_target", lambda explicit=None: "/tmp/fake-t7813.db")
    return _FakeSDK


def _run_decide(decide_input: str, extra_args: list[str]) -> list[dict]:
    rc = m.main(["decide", "--input", decide_input, *extra_args])
    assert rc == 0, f"decide exited {rc}"
    assert _FakeSDK.instances, "decide constructed no SDK"
    return _FakeSDK.instances[-1].operator_calls


def test_direction_reaches_all_three_call_sites(fake_sdk, tmp_path):
    """`--direction unidirectional` reaches edges, truth_edges AND relevance_edges."""
    calls = _run_decide(_write_input(tmp_path), ["--direction", "unidirectional"])

    # 2 regular edges + 1 truth edge + 1 relevance edge
    assert len(calls) == 4, calls
    assert [c["op_type"] for c in calls].count("NAND") == 3
    assert [c["op_type"] for c in calls].count("IMPL") == 1
    # every call site received the chosen direction — a fix that threaded only
    # the first loop would fail here.
    for call in calls:
        assert call["kwargs"].get("direction") == "unidirectional", call


def test_default_omits_direction_kwarg(fake_sdk, tmp_path):
    """With no `--direction`, the kwarg is omitted entirely (not passed as
    None), so `create_operator`'s own default applies and #807 is intact."""
    calls = _run_decide(_write_input(tmp_path), [])

    assert len(calls) == 4, calls
    for call in calls:
        assert "direction" not in call["kwargs"], call


def _run_expecting_refusal(argv: list[str]) -> int:
    """Return a non-zero exit code whether the parser raises or returns.

    `main` catches an unknown-option `ArgumentError` and returns 2, while an
    invalid `choices` value surfaces as `SystemExit` — the refusal contract is
    the same either way, so normalise both shapes here.
    """
    try:
        rc = m.main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return rc


def test_direction_choices_stay_sdk_bounded(fake_sdk, tmp_path):
    """Only the two values `create_operator` accepts are legal.

    Widening the accepted set (`->`, `<-`, `<->`, `-`) is #7852, not this fix;
    a typo must be refused rather than silently persisted.
    """
    rc = _run_expecting_refusal(["decide", "--input", _write_input(tmp_path), "--direction", "->"])
    assert rc != 0
    # Refused at the parser, before any SDK write: nothing was persisted.
    assert not _FakeSDK.instances


# ── Live lane: prove the stored operator property, not just the kwarg ───────


_LIVE_SKIP = pytest.mark.skipif(
    not _live_utils.docker_reachable(),
    reason="live FalkorDB (Docker) not available",
)


def _stored_directions(uri: str) -> list[str | None]:
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection.from_uri(uri)
    try:
        rows = proj.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true RETURN o.direction"
        ).result_set
        return [r[0] for r in rows]
    finally:
        proj.close()


@_LIVE_SKIP
def test_default_stores_bidirectional_live(tmp_path, monkeypatch):
    """End-to-end: no `--direction` stores `bidirectional` (the #807 default).

    This is the literal acceptance check — the unit test only proves the kwarg
    is omitted; here the real SDK persists the value.
    """
    monkeypatch.setenv("TORTOISE_EP_REQUIRE_CALIBRATION", "0")
    graph = f"tortoise_test_t7813_default_{uuid.uuid4().hex[:8]}"
    uri = _live_utils.docker_uri(graph)

    rc = m.main(["decide", "--input", _write_input(tmp_path), "--db", uri])
    assert rc == 0

    directions = _stored_directions(uri)
    assert directions, "no operators were written"
    assert set(directions) == {"bidirectional"}, directions


@_LIVE_SKIP
def test_explicit_none_guard_stays_reachable():
    """The `if direction is None` guard in `create_operator` is LIVE, not dead.

    The SDK's own ingest path (`conn.get("direction")` -> None when absent) and
    `battery/runner/setup.py::naive_setup` both pass an explicit None, which
    canonicalizes NAND -> `unidirectional` under CYCLE-25. Pinning that here is
    what justifies KEEPING the guard: the CLI deliberately does not route its
    *unspecified* case through it, because that would flip the default and
    reverse #807/#753/#86.
    """
    from tortoise.sdk import TortoiseSDK

    uri = _live_utils.docker_uri(f"tortoise_test_t7813_guard_{uuid.uuid4().hex[:8]}")
    sdk = TortoiseSDK(db_path=None, namespace=None)
    sdk._db_uri = uri
    sdk._proj = None  # force re-init against the isolated test graph
    try:
        a = sdk.create_point("statement", "a", status="live")
        b = sdk.create_point("statement", "b", status="live")
        op = sdk.create_operator("NAND", a["id"], [b["id"]], direction=None)
        proj = sdk._get_proj()
        stored = proj.g.query(
            "MATCH (o:Point {id:$id}) RETURN o.direction",
            params={"id": op["id"]},
        ).result_set[0][0]
    finally:
        sdk.close()

    assert stored == "unidirectional", (
        "explicit direction=None must still canonicalize NAND per CYCLE-25 — "
        "the guard is reachable and must not be removed as dead code"
    )


@_LIVE_SKIP
def test_explicit_unidirectional_is_logged_live(tmp_path, monkeypatch):
    """End-to-end: `--direction unidirectional` is what the graph records."""
    monkeypatch.setenv("TORTOISE_EP_REQUIRE_CALIBRATION", "0")
    graph = f"tortoise_test_t7813_directed_{uuid.uuid4().hex[:8]}"
    uri = _live_utils.docker_uri(graph)

    rc = m.main(
        [
            "decide",
            "--input",
            _write_input(tmp_path),
            "--direction",
            "unidirectional",
            "--db",
            uri,
        ]
    )
    assert rc == 0

    directions = _stored_directions(uri)
    assert directions, "no operators were written"
    assert set(directions) == {"unidirectional"}, directions
