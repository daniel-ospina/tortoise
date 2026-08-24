"""#1162 — EventAPI.add_operator must NOT run the global extract_svbp_factors
scan when the deprecated SVBP path is live (jax/quadrature extra installed).

Every operator write previously triggered an O(graph) factor scan + a
5-iteration SVBP warm-start over the whole factor set (tortoise/api.py,
Gate 4 block). The fix scopes the warm-start factor set to the NEW operator —
fully known in add_operator's scope as (op_id, op_type, [inputs], weight),
with the extract_svbp_factors weight rule (3.0 NAND / 1.0 IMPL) and its
>=2-inputs degenerate exclusion. Per-write cost: O(graph) -> O(1), zero graph
queries.

SVBP is deprecated (EP — tortoise/ep.py — is the shipping path) but the
incremental warm-start semantics (Bug 4 max_iter restore, Bug 5 lazy-init)
are preserved at O(1). The jax-absent degrade-to-None path (get_svbp -> None)
is unchanged: no scan, no SVBP.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._embedded import _wipe_or as wipe  # noqa: E402, I001, RUF100

from tortoise.api import EventAPI, provenance  # noqa: E402, RUF100
from tortoise.log import EventLog  # noqa: E402, RUF100


def _fresh(shared_proj):
    """One session-shared embedded projection (#1012) + EventAPI (api surface).
    Wipe per test — hermeticity comes from the wipe, not fresh paths."""
    wipe(shared_proj)
    log = EventLog(os.path.join(tempfile.mkdtemp(prefix="tt_1162_"), "e.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=shared_proj)
    return api, shared_proj


class _RecSVBP:
    """Recording stand-in for TortoiseSVBP (the jax/quadrature live path).

    Implements the surface add_operator touches: run(factors, evidence,
    warm_start) + the max_iter knob the Bug 4 fix restores after the run.
    """

    def __init__(self):
        self.max_iter = 40
        self.runs = []  # (factors, evidence, warm_start)

    def run(self, factors, evidence=None, warm_start=False):
        self.runs.append((factors, evidence, warm_start))
        return (1, True)

    def compute_confidence(self, claim_id):
        return {"mean": 0.5, "variance": 1 / 12, "alpha": 1.0, "beta": 1.0}


def _spy_extract(proj):
    """Record any call to the GLOBAL extract_svbp_factors (the #1162 bug)."""
    calls = []
    orig = proj.extract_svbp_factors
    proj.extract_svbp_factors = lambda *a, **k: (calls.append(1) or orig(*a, **k))
    return calls


def _two_claims(api):
    prov = provenance("doc.txt", [0, 10], "quote", extracted_by="test@0")
    a = api.add_point("claim a", prov)
    b = api.add_point("claim b", prov)
    return prov, a, b


def test_add_operator_does_not_scan_whole_graph_when_svbp_present(shared_proj):
    """#1162 — an operator write with SVBP live must NOT call the GLOBAL
    extract_svbp_factors; the warm-start factor set is the new operator's own
    (op_id, op_type, [inputs], weight) tuple."""
    if shared_proj is None:
        return
    api, proj = _fresh(shared_proj)
    rec = _RecSVBP()
    api._svbp = rec  # jax/quadrature path already initialized
    calls = _spy_extract(proj)

    prov, a, b = _two_claims(api)
    op_id = api.add_operator("IMPL", [a, b], prov)

    assert calls == [], (
        f"add_operator must NOT call global extract_svbp_factors (#1162), "
        f"got {len(calls)} call(s)")
    assert len(rec.runs) == 1, rec.runs
    factors, _evidence, warm_start = rec.runs[0]
    assert warm_start is True
    assert factors == [(op_id, "IMPL", [a, b], 1.0)], factors
    assert rec.max_iter == 40, "max_iter must be restored after warm-start (Bug 4 fix)"


def test_add_operator_nand_local_factor_weight(shared_proj):
    """#1162 — NAND keeps the extract_svbp_factors weight rule (3.0) in the
    scoped warm-start factor."""
    if shared_proj is None:
        return
    api, proj = _fresh(shared_proj)
    rec = _RecSVBP()
    api._svbp = rec
    calls = _spy_extract(proj)

    prov, a, b = _two_claims(api)
    op_id = api.add_operator("NAND", [a, b], prov)

    assert calls == []
    assert rec.runs[0][0] == [(op_id, "NAND", [a, b], 3.0)], rec.runs[0][0]


def test_add_operator_degenerate_input_skips_warm_start(shared_proj):
    """#1162 — a <2-input operator matches extract_svbp_factors' degenerate
    exclusion: no factor is passed to the SVBP warm-start (and no global
    scan runs)."""
    if shared_proj is None:
        return
    api, proj = _fresh(shared_proj)
    rec = _RecSVBP()
    api._svbp = rec
    calls = _spy_extract(proj)

    prov = provenance("doc.txt", [0, 10], "quote", extracted_by="test@0")
    a = api.add_point("claim a", prov)
    api.add_operator("IMPL", [a], prov)

    assert calls == [], "degenerate add_operator must not scan the graph either"
    assert rec.runs == [], "degenerate operator must be excluded from the warm-start"


def test_add_operator_get_svbp_none_degrade_preserved(shared_proj, monkeypatch):
    """#1162 — jax absent (get_svbp degrades to None): add_operator works and
    never scans the graph — the degrade-to-None path is unchanged (AC2)."""
    if shared_proj is None:
        return
    api, proj = _fresh(shared_proj)
    monkeypatch.setattr(proj, "get_svbp", lambda **k: None)
    calls = _spy_extract(proj)

    prov, a, b = _two_claims(api)
    op_id = api.add_operator("IMPL", [a, b], prov)

    assert calls == [], "no-jax path must never call extract_svbp_factors"
    assert api._svbp is None, "get_svbp -> None must leave _svbp unset"
    assert op_id
