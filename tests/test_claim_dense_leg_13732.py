"""Pins what #13732's measurement rests on — the half that runs with no store.

The lane's question is *"does the record's dense leg lift claim recall?"*, and the
instrument (`tools/claim_dense_leg_13732.py`) cannot answer it on a box with no
corpus, no embedder and no Postgres. What CAN be pinned everywhere is the part a
later run must not be free to choose after it sees the numbers:

1. **the pre-registered statistic** — the paired McNemar and the DETECTABLE
   EFFECT at the population's n. A lane that discovers its delta is 2 points and
   then picks a test that calls that significant is the failure this file makes
   impossible: the numbers are constants here, and the tool's own
   `--preregister` output is compared against them;
2. **the arms' own claims about themselves** — `record_dense` must be served by
   `mechanism=pgvector` and the lexical arm must show a dense leg that did not
   run. A `scan_fallback` on the record arm is REFUSED here, because a graph
   answer wearing the record arm's name is the one way this measurement could
   be reported and be about the wrong store;
3. **the coverage gate's zero trap** — `coverage` compares
   `record_vectors >= graph_vectors`, and **0 >= 0 is verified**, so an unseeded
   population PASSES it and is served by a leg whose trace never names the
   two-zero state (`empty_results` from the record where the contract owns the
   label, `no_embeddings`/`degraded=True` from the graph elsewhere — this
   instrument reads `point`, so the second shape is the one it sees). ⛔ M2's
   population had no vectors because **its box had no embedder**, NOT because its
   write payload lacked an `embedding` key — `_upsert_point_props` DERIVES one
   from `content` (`projection/entities.py:2968-2976`, inside an
   `except Exception: pass`), so the same payload on a box with an embedder would
   have carried vectors. The guard is asserted rather than assumed either way;
3b. **and the ID-SCOPED half of that guard** — `coverage` is a LABEL-WIDE count
   by design (`#11395` step 2), so it verifies any tenant that holds *some*
   embedded `Point` rows, including one that holds NONE of THIS population's ids.
   `record_population_vectors` must ask the record for the population's OWN id
   set before an arm runs: without it `record_dense` ranks nothing and publishes
   `recall@3 = 0.0` as a finding about the leg instead of a finding about the
   seed step;
4b. **the DSN comes from the module that owns it** — `record_vector` declares the
   ARM, not the DSN (`record_writer.record_dsn` does). The shipped call read
   `record_vector.record_dsn`, i.e. an `AttributeError` one expression BEFORE the
   `no_record_dsn` refusal the artifact promises, on exactly the documented
   recipe (`--dsn` defaults to `""`, the environment carries the value);
4. **the contradiction test, as a structural pin** — the dense leg comes FROM
   THE RECORD (`#11481` V1: Postgres is the record, FalkorDB is the projection).
   The instrument must therefore never create a graph vector index nor seed
   record rows; an AST scan over its CODE literals refuses either. A future lane
   that "fixes" the
   measurement by indexing `:Object` on the graph would be reversing a landed
   decision, and this test is where that shows up.

⛔ Nothing here runs the measurement. The live arms need a capture corpus, an
embedder (`huggingface` reachable) and a pgvector record — the instrument refuses
by NAME (`no_corpus` / `no_embedder` / `no_record_dsn` / `coverage_unverified`)
rather than publishing a fixture's number, and those refusals are asserted below
so a silent skip cannot take their place.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import os
import re
import shlex
import subprocess
import sys
import threading
import types
from pathlib import Path
from typing import ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "claim_dense_leg_13732.py"

#: M2's built claim population (`--points 900` -> 786 built), the n the lane's
#: pre-registration is stated for.
M2_N = 786


@pytest.fixture(scope="module")
def tool_mod():
    assert TOOL.exists(), f"missing instrument {TOOL}"
    spec = importlib.util.spec_from_file_location("claim_dense_leg_13732", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _JournalDouble:
    """An `EventLog` that proves a position for every append, like the real one.

    ``TORTOISE_RECORD_JOURNAL`` / ``--journal`` name a PATH, and the real
    `EventLog` counts the journal to answer each append's ordinal. The seed only
    needs the contract — append returns the record's 1-based position — so the
    double counts its own appends, which makes "the record was handed the
    journal's OWN position" (`seq`) an assertion instead of a guess.
    """

    durable = True

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.positions: list[int] = []

    def append(self, event: dict) -> int:
        self.events.append(event)
        self.positions.append(len(self.events))
        return self.positions[-1]


class _FoldDouble:
    """The graph fold the chokepoint applies — records what it was handed."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def apply(self, event: dict) -> None:
        self.events.append(event)


def _journal_double(monkeypatch) -> _JournalDouble:
    """Install a journal double for `from tortoise.log import EventLog`."""
    log = _JournalDouble()
    monkeypatch.setattr("tortoise.log.EventLog", lambda _path: log)
    return log


# ── 1. the pre-registered statistic ────────────────────────────────────────


class TestPreregistration:
    """The thresholds exist BEFORE the numbers, and are constants, not choices."""

    #: Computed by hand from `delta = (z_{0.975} + z_{0.80})·sqrt(p_d/n)` at
    #: n = 786 — the same expression the tool implements, pinned independently so
    #: a "tuned" formula reds here.
    EXPECTED_PP: ClassVar[dict[float, float]] = {
        0.05: 2.23,
        0.10: 3.16,
        0.20: 4.47,
        0.30: 5.47,
    }

    def test_detectable_effect_at_the_m2_population(self, tool_mod):
        for discordance, expected in self.EXPECTED_PP.items():
            got = tool_mod.detectable_effect_pp(M2_N, discordance)
            assert abs(got - expected) < 0.06, (discordance, got, expected)

    def test_detectable_effect_is_monotone_in_discordance(self, tool_mod):
        vals = [tool_mod.detectable_effect_pp(M2_N, d) for d in (0.05, 0.10, 0.20, 0.30)]
        assert vals == sorted(vals)

    def test_detectable_effect_falls_as_n_grows(self, tool_mod):
        small = tool_mod.detectable_effect_pp(786, 0.2)
        big = tool_mod.detectable_effect_pp(786 * 4, 0.2)
        assert big < small

    def test_erfinv_inverts_erf(self, tool_mod):
        for x in (-0.9, -0.5, 0.0, 0.3, 0.75):
            assert abs(math.erf(tool_mod._erfinv(x)) - x) < 1e-9

    def test_preregistration_block_is_runnable_and_carries_the_numbers(self, tool_mod):
        block = tool_mod.preregistration(M2_N)
        assert block["population_n"] == M2_N
        assert block["primary_k"] == 3
        assert set(block["detectable_delta_pp"]) == {"0.05", "0.10", "0.20", "0.30"}
        assert block["statistic"].startswith("exact two-sided McNemar")
        assert block["arms"] == ["lexical", "graph_dense", "record_dense"]
        assert block["declared_mechanisms"]["record_dense"] == ["pgvector"]

    def test_the_docstring_table_quotes_the_same_numbers(self, tool_mod):
        """ONE spelling of each pre-registered threshold, in prose and in code.

        ⛔ `test_detectable_effect_at_the_m2_population` allows ±0.06 — which
        cannot separate 2.23 from 2.24 — so the module docstring's own copy of
        the table could drift from the computation and stay green. It did: the
        docstring quoted `2.24` where the committed block, the PR body and the
        test constant all say `2.23`. A pre-registration whose quoted threshold
        exists in two spellings is the one artifact where that is not a nit, so
        the table is read OUT of the docstring and compared here.
        """
        rows = re.findall(r"^(0\.\d{2})\s+(\d+\.\d{2})$", tool_mod.__doc__ or "", flags=re.M)
        assert len(rows) == 4, f"expected the 4-row table, read {rows}"
        for discordance, quoted in rows:
            assert float(quoted) == round(
                tool_mod.detectable_effect_pp(M2_N, float(discordance)), 2
            ), (discordance, quoted)

    def test_the_preregistration_needs_no_statistics_stack(self):
        """The reason `_erfinv` is hand-rolled — the fact prose cannot carry.

        ⛔ `scipy>=1.10` IS a direct dependency (`pyproject.toml`), so an earlier
        docstring's "scipy is not in the base install" was false and is the kind
        of wrong reason the next lane copies. What must stay true is that the
        instrument imports no such stack: two fixed normal quantiles are computed
        from `math`, so the pre-registration runs wherever `python` does.
        """
        tree = ast.parse(TOOL.read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "scipy" not in imported, imported
        assert "numpy" not in imported, imported

    def test_committed_preregistration_matches_the_instrument(self, tool_mod):
        """The committed block and the tool's own output are the SAME numbers.

        ⛔ A COMPOSITION check, not provenance: it says the artifact was not
        edited independently of the code that emits it, so an edit to one that
        stales the other reds. It cannot show the thresholds existed before the
        run — only the run's own ordering does that, which is why the file is
        committed WITH the instrument rather than after a measurement.
        """
        committed = json.loads(
            (
                REPO_ROOT / "docs" / "evidence" / "13732-claim-dense-leg" / "preregistration.json"
            ).read_text()
        )
        assert committed == tool_mod.preregistration(M2_N)

    def test_the_readme_table_quotes_the_committed_thresholds(self, tool_mod):
        """The README's §1 table is a SECOND copy, and a docs-only edit re-stales it.

        ⛔ `config/ci-surfaces.yml` names the README as one of the guarded
        artifacts, and until this pin existed nothing read it: the README copied
        the four detectable-effect values into a markdown table, so a docs-only
        edit could move them with every pin green — the "coverage claim with no
        pin behind it" the sibling rows call out. Compared CELL BY CELL against
        the same committed block the pin above checks.
        """
        readme = (
            REPO_ROOT / "docs" / "evidence" / "13732-claim-dense-leg" / "README.md"
        ).read_text()
        committed = json.loads(
            (
                REPO_ROOT / "docs" / "evidence" / "13732-claim-dense-leg" / "preregistration.json"
            ).read_text()
        )
        rows = re.findall(r"^\|\s*([0-9.]+)\s*\|\s*\*\*([0-9.]+) pp\*\*\s*\|\s*$", readme, re.M)
        assert rows, "the README's detectable-effect table is gone or was reworded"
        assert {d: float(v) for d, v in rows} == {
            d: float(v) for d, v in committed["detectable_delta_pp"].items()
        }, "the README table and preregistration.json disagree"
        # …and the population the table's right-hand column names in prose.
        assert f"at n = {committed['population_n']}" in readme

    def test_preregister_needs_no_store_no_embedder_no_corpus(self):
        """The one mode THIS box can run — so the thresholds precede the run.

        Run in a SUBPROCESS with an empty environment so an embedder, a DSN or a
        live FalkorDB in the parent process cannot make this pass by accident.
        """
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--preregister", "--population", str(M2_N)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        )
        assert proc.returncode == 0, proc.stderr
        block = json.loads(proc.stdout)
        assert block["detectable_delta_pp"]["0.20"] == pytest.approx(4.47, abs=0.06)


# ── 2. the paired analysis ─────────────────────────────────────────────────


class TestPaired:
    def _ranks(self, spec: dict[str, int | None]) -> dict[str, int | None]:
        return dict(spec)

    def test_rank_of_absent_is_none_not_zero(self, tool_mod):
        assert tool_mod.rank_of(["a", "b"], "b") == 2
        assert tool_mod.rank_of(["a", "b"], "z") is None
        assert tool_mod.hit(None, 10) is False
        assert tool_mod.hit(0, 1) is False  # a 0 rank is not a rank

    def test_paired_counts_discordant_pairs_only(self, tool_mod):
        # b gains 3, loses 1 -> net +2 of 10 queries; the 6 concordant carry no
        # information and must not enter the test's denominator.
        a = {
            "q0": 1,
            "q1": 1,
            "q2": None,
            "q3": None,
            "q4": 9,
            "q5": None,
            "q6": 1,
            "q7": 1,
            "q8": None,
            "q9": None,
        }
        b = {
            "q0": 1,
            "q1": 1,
            "q2": 2,
            "q3": 3,
            "q4": 9,
            "q5": 1,
            "q6": 1,
            "q7": 5,
            "q8": None,
            "q9": None,
        }
        cmp = tool_mod.paired(a, b, 3)
        assert cmp["only_a"] == 1  # q7 (rank 5) lost
        assert cmp["only_b"] == 3  # q2, q3, q5 gained
        assert cmp["discordant"] == 4
        assert cmp["delta_pp"] == pytest.approx(20.0)
        assert cmp["found_deep_a"] == 5  # q0,q1,q4,q6,q7 at any rank
        assert cmp["found_deep_b"] == 8  # every query but q8,q9

    def test_paired_refuses_different_id_sets(self, tool_mod):
        with pytest.raises(ValueError):
            tool_mod.paired({"a": 1}, {"b": 1}, 3)

    def test_mcnemar_is_exact_and_matches_scipy(self, tool_mod):
        binomtest = pytest.importorskip("scipy.stats").binomtest
        for only_b, n_disc in ((3, 4), (10, 20), (0, 7), (25, 40)):
            got = tool_mod._mcnemar_p(only_b, n_disc)
            want = binomtest(only_b, n_disc, 0.5, alternative="two-sided").pvalue
            assert got == pytest.approx(min(1.0, want), abs=1e-6), (only_b, n_disc)

    def test_unresolved_when_the_delta_is_below_the_detectable_effect(self, tool_mod):
        # n = 786, 20 % discordance -> detectable 4.47 pp. A 2 pp move with a
        # small p is still UNRESOLVED: the test can see an ordering, not a size.
        cmp = {
            "delta_pp": 2.0,
            "mcnemar_p_two_sided": 0.01,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": 0.0,
        }
        assert tool_mod.classify(cmp) == "UNRESOLVED"

    def test_trade_is_not_a_gain(self, tool_mod):
        cmp = {
            "delta_pp": 8.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": -2.5,
        }
        assert tool_mod.classify(cmp) == "TRADE"

    def test_a_double_negative_is_a_loss_not_a_trade(self, tool_mod):
        """⛔ ``TRADE`` is a GAIN that costs coverage — never a move that loses BOTH.

        The committed ``trade_rule`` says *"a GAIN at k=3 that lowers
        found@deep"*. Gating ``TRADE`` on ``found_deep_delta_pp < 0`` alone
        labelled a plain regression as a trade, which is the softer word for it —
        and it is the field the artifact quotes as the verdict.
        """
        cmp = {
            "delta_pp": -8.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": -2.5,
        }
        assert tool_mod.classify(cmp) == "LOSS"

    def test_a_larger_run_cannot_buy_an_easier_bar(self, tool_mod):
        """⛔ A corpus that builds MORE claims must not tighten its own bar.

        The observed bar falls as n grows, so gating on it ALONE would let a run
        with a bigger population call the same absolute move a ``GAIN`` under a
        threshold the pre-registration never declared. The pre-registered bar
        binds in that direction — and the run's own bar binds in the other (see
        ``test_a_run_below_the_preregistered_n_cannot_buy_an_easier_bar``), which
        is why the gate is ``max`` of the two and not "the pre-registered one".
        """
        cmp = {
            "delta_pp": 4.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 3.23,  # observed at a larger n
            "detectable_pp_preregistered": 4.47,  # committed at n = 786
            "found_deep_delta_pp": 0.0,
        }
        assert tool_mod.classify(cmp) == "UNRESOLVED"
        # …and with the move above the COMMITTED bar it resolves.
        cmp["delta_pp"] = 5.0
        assert tool_mod.classify(cmp) == "GAIN"

    def test_a_run_below_the_preregistered_n_cannot_buy_an_easier_bar(self, tool_mod):
        """⛔ ``detectable_effect_pp`` DECREASES in n, so the pre-registered bar is
        the larger one only when the run built MORE claims than 786.

        `--min-population` is 600, so `600 <= built_n < 786` is reachable from the
        documented recipe — and there the pre-registered bar (4.47) is SMALLER
        than the run's own resolution (4.74 at n = 700). Gating on the
        pre-registered bar alone would publish a delta the run cannot resolve as
        a `GAIN`: the exact inversion of the class this gate closes. The bar is
        therefore max(pre-registered, observed).
        """
        observed = tool_mod.detectable_effect_pp(700, 0.20)
        pre_registered = tool_mod.detectable_effect_pp(786, 0.20)
        assert pre_registered < observed, "the fixture must undersize the run"
        cmp = {
            "delta_pp": round((pre_registered + observed) / 2, 2),  # between the two
            "mcnemar_p_two_sided": 1e-6,
            "detectable_pp": round(observed, 2),
            "detectable_pp_preregistered": round(pre_registered, 2),
            "found_deep_delta_pp": 1.0,
        }
        # the delta clears the pre-registered bar and NOT the run's own.
        assert cmp["delta_pp"] > pre_registered and cmp["delta_pp"] < observed
        assert tool_mod.classify(cmp) == "UNRESOLVED"

    def test_the_emitted_bar_is_the_one_the_gate_used(self, tool_mod):
        """⛔ The artifact EMITS the deciding bar, so it must be the gate's own.

        `detectable_pp_gated` carried `detectable_pp_preregistered`
        unconditionally while `classify` gated on `max(...)` — so on the
        `600 <= built_n < 786` band the artifact named a bar the label had not
        used, and the emitted `population_mismatch.note` pointed a reader AT that
        field as "the one that decided". One home for both, and it is
        `gated_bar_pp`.
        """
        for cmp in (
            {"detectable_pp": 5.12, "detectable_pp_preregistered": 4.47},
            {"detectable_pp": 3.23, "detectable_pp_preregistered": 4.47},
            {"detectable_pp": 4.47, "detectable_pp_preregistered": None},
            {"detectable_pp": None, "detectable_pp_preregistered": None},
        ):
            bars = [b for b in (cmp["detectable_pp"], cmp["detectable_pp_preregistered"]) if b]
            assert tool_mod.gated_bar_pp(cmp) == (max(bars) if bars else None), cmp

    def test_no_text_still_says_the_preregistered_bar_alone_decides(self, tool_mod):
        """The superseded rule, pinned as ABSENT — three statements carried it.

        `bd349877` took `max(...)`; `paired`'s docstring, the `preregistered_n`
        comment and the artifact's own `population_mismatch.note` kept saying the
        pre-registered bar decides and is "the harder bar at the larger n" —
        false for the `600 <= built_n < 786` range the fix was written for, and
        the note is a field the artifact publishes.
        """
        src = TOOL.read_text()
        for stale in (
            "THE PRE-REGISTERED ONE IS THE GATE",
            "the PRE-REGISTERED bar still decides",
            "harder bar at the larger n",
            "the observed one is DISCLOSURE",
        ):
            assert stale not in src, f"the superseded bar rule survives: {stale!r}"
        assert "STRICTER" in tool_mod.BAR_RULE, "BAR_RULE must state the rule"

    def test_gain_and_loss(self, tool_mod):
        up = {
            "delta_pp": 8.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": 1.0,
        }
        down = {
            "delta_pp": -8.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": 0.0,
        }
        assert tool_mod.classify(up) == "GAIN"
        assert tool_mod.classify(down) == "LOSS"


# ── 3. the arms' claims about themselves ───────────────────────────────────


def _trace(
    mechanism: str | None, *, ran: bool = True, degraded: bool = False, reason: str | None = "ok"
) -> list[dict]:
    entry = {"leg": "vector", "ran": ran, "degraded": degraded, "reason": reason, "count": 1}
    if mechanism is not None:
        entry["mechanism"] = mechanism
    return [entry, {"leg": "fts", "ran": True, "degraded": False, "reason": "ok", "count": 5}]


class TestArmAssertions:
    def test_record_arm_accepts_pgvector(self, tool_mod):
        got = tool_mod.check_arm("record_dense", _trace("pgvector"))
        assert got == {"arm": "record_dense", "ran": True, "mechanisms": ["pgvector"]}

    def test_record_arm_refuses_scan_fallback(self, tool_mod):
        """A graph answer wearing the record's name — the falsifier's whole point."""
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("record_dense", _trace("scan_fallback"))
        assert ei.value.code == "record_dense_not_pgvector"
        assert ei.value.exit_code == 3

    def test_record_arm_refuses_a_mixed_run(self, tool_mod):
        mixed = _trace("pgvector") + _trace("scan_fallback")
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("record_dense", mixed)
        assert ei.value.code == "record_dense_mixed_mechanism"

    def test_record_arm_refuses_when_the_dense_leg_did_not_run(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm(
                "record_dense", _trace("pgvector", ran=False, degraded=True, reason="query_failed")
            )
        assert ei.value.code == "dense_arm_did_not_run"

    def test_record_arm_refuses_a_dense_leg_that_ran_and_DEGRADED(self, tool_mod):
        """⛔ THE ENGINE'S REAL FAILURE SHAPE — `ran=True, degraded=True`.

        `search_engine.py:4153` (record leg) and `:4742` (graph scan) both record
        `ran=True, degraded=True, reason="query_failed"` **with the mechanism
        still set**, and `:3491-3496` forbids a mechanism on a `ran=False`
        entry — so the fixture above (`ran=False` + `mechanism`) is a shape
        `_trace_entry` cannot emit. Pre-fix, `check_arm` decided from `ran`
        alone and ACCEPTED this entry: 786 guaranteed misses would be scored as
        the arm's recall, and the gate that exists to stop exactly that passed
        it. `require_hybrid_read` (`:2749-2757`) is the shipped predicate this
        now matches: a `degraded=True` vector leg contributed no semantic
        results.
        """
        for arm, mechanism in (("record_dense", "pgvector"), ("graph_dense", "scan")):
            with pytest.raises(tool_mod.Refused) as ei:
                tool_mod.check_arm(
                    arm, _trace(mechanism, ran=True, degraded=True, reason="query_failed")
                )
            assert ei.value.code == "dense_arm_degraded_read", arm
            assert ei.value.exit_code == 3
            assert "query_failed" in ei.value.message, ei.value.message

    def test_record_arm_refuses_a_MIXED_healthy_and_degraded_run(self, tool_mod):
        """Some queries healthy, some failed — all `pgvector`, so the mechanism
        gate cannot see it, and the failed queries would score as misses."""
        mixed = _trace("pgvector") + _trace(
            "pgvector", ran=True, degraded=True, reason="query_failed"
        )
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("record_dense", mixed)
        assert ei.value.code == "dense_arm_degraded_read"
        assert "1 of 2" in ei.value.message, ei.value.message

    def test_a_healthy_dense_trace_is_still_accepted(self, tool_mod):
        """The strictness must not refuse the read it is measuring."""
        got = tool_mod.check_arm(
            "record_dense", _trace("pgvector") + _trace("pgvector", reason="ok")
        )
        assert got == {"arm": "record_dense", "ran": True, "mechanisms": ["pgvector"]}

    def test_lexical_arm_accepts_a_dense_leg_that_did_not_run(self, tool_mod):
        got = tool_mod.check_arm(
            "lexical", _trace(None, ran=False, degraded=True, reason="no_embedder")
        )
        assert got["ran"] is False

    def test_lexical_arm_refuses_a_hybrid_read(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("lexical", _trace("index"))
        assert ei.value.code == "lexical_arm_ran_dense"

    def test_graph_arm_refuses_the_record_mechanism(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("graph_dense", _trace("pgvector"))
        assert ei.value.code == "graph_arm_answered_from_record"

    def test_graph_arm_accepts_the_index_and_the_scan(self, tool_mod):
        assert tool_mod.check_arm("graph_dense", _trace("index"))["mechanisms"] == ["index"]
        assert tool_mod.check_arm("graph_dense", _trace("scan_fallback"))["mechanisms"] == [
            "scan_fallback"
        ]

    def test_graph_arm_refuses_a_dense_leg_that_names_NO_mechanism(self, tool_mod):
        """⛔ `ran=True` with no mechanism certified the control with no evidence.

        The arm exists to prove WHERE the leg came from. An empty mechanism set
        passed the other checks VACUOUSLY — nothing unknown, nothing from the
        record — and published `trace_verdict: {"ran": true, "mechanisms": []}`.
        Measured pre-fix: `check_arm("graph_dense", [{"leg": "vector", "ran":
        True, ...}])` returned `{"arm": "graph_dense", "ran": True,
        "mechanisms": []}` while `record_dense` refused the same entry.
        """
        with pytest.raises(tool_mod.Refused) as exc:
            tool_mod.check_arm(
                "graph_dense",
                [{"leg": "vector", "ran": True, "degraded": False, "reason": "ok", "count": 3}],
            )
        assert exc.value.code == "graph_arm_no_mechanism"

    def test_a_trace_with_no_vector_entry_is_refused_not_passed(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("record_dense", [{"leg": "fts", "ran": True}])
        assert ei.value.code == "trace_missing_vector_leg"


# ── 4. the preconditions refuse BY NAME ────────────────────────────────────


class TestRefusals:
    def test_missing_corpus_names_the_directory(self, tool_mod, tmp_path):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_corpus(tmp_path)
        assert ei.value.code == "no_corpus"
        assert str(tmp_path) in ei.value.message
        assert "fixture" in ei.value.message

    def test_no_record_dsn(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_record(None)
        assert ei.value.code == "no_record_dsn"

    def test_the_dsn_is_read_through_the_module_that_owns_it(self, tool_mod, monkeypatch):
        """`record_vector` declares the ARM; `record_writer` declares the DSN.

        ⛔ The shipped call was `record_vector.record_dsn()` — an attribute that
        does not exist, so the documented recipe (no `--dsn`, the value in
        `TORTOISE_RECORD_DSN`) raised `AttributeError` one expression BEFORE the
        `no_record_dsn` refusal, and AFTER the graph had already been seeded.
        """
        source = TOOL.read_text()
        assert "record_vector.record_dsn" not in source
        tree = ast.parse(source)
        # ⛔ ANY `<module>.record_dsn` attribute read is the defect shape: the
        # function is `record_writer`'s, and asking `record_vector` (or an alias
        # of it) for it is the AttributeError. A text pin alone missed an alias.
        off_module = [
            n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "record_dsn"
        ]
        assert not off_module, [ast.dump(n)[:80] for n in off_module]
        assert "from tortoise.record_writer import" in source
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        assert any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_resolve_dsn"
            for n in ast.walk(run)
        ), "run_measurement must resolve the DSN through `_resolve_dsn`"
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "")
        assert tool_mod._resolve_dsn("") is None  # unset/blank is UNARMED, not an error
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@record.example/db")
        assert tool_mod._resolve_dsn("") == "postgresql://u:p@record.example/db"

    def test_a_malformed_dsn_is_a_named_refusal_not_a_traceback(self, tool_mod, monkeypatch):
        """A configured-but-unusable DSN is a misconfiguration, refused BY NAME.

        The seam raises `RecordWriteError` (wrong scheme, no host, whitespace);
        `main()` catches only `Refused`, so without this conversion the operator
        would get a traceback where the artifact promises a named precondition.
        """
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "mysql://user@host/db")
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod._resolve_dsn("")
        assert ei.value.code == "record_dsn_unusable"
        assert ei.value.exit_code == 2

    def test_seeded_guard_refuses_the_zero_zero_coverage_trap(self, tool_mod):
        """`0 >= 0` is VERIFIED — so seeding is guarded, not inferred.

        ⛔ AND IT IS GUARDED FROM BOTH SIDES, BY TWO FUNCTIONS. One function that
        asserted both counts was called inside the record block's `try/except`,
        so a zero-vector GRAPH raised `graph_unseeded` there and blocked the
        RECORD arm — a graph fact wearing a record cause, with the headline
        `record_dense_vs_lexical` comparison going UNMEASURED (`#13745` review
        round 2, finding 1). Each side now refuses where its fact lives.
        """
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_graph_population(0, 786)
        assert ei.value.code == "graph_population_absent"
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_record_measured(0)
        assert ei.value.code == "record_unseeded"

    def test_the_record_guard_never_names_the_graph(self, tool_mod):
        """⚠️ THE MESSAGE IS THE DIAGNOSIS: a record refusal must not blame the graph.

        `require_seeded`'s graph clause said *"every arm would measure the lexical
        leg"* — false for `record_dense`, which reads Postgres — and it raised
        from inside the record block, so that sentence shipped as the record
        arm's explanation.
        """
        source = TOOL.read_text()
        tree = ast.parse(source)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "require_record_measured"
        )
        # ⛔ THE CODE, NOT THE DOCSTRING: the docstring must be free to EXPLAIN the
        # old coupling, and a pin that forbade the word there would forbid the
        # explanation of the bug it guards.
        code_nodes = (
            fn.body[1:]
            if (
                fn.body
                and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)
                and isinstance(fn.body[0].value.value, str)
            )
            else fn.body
        )
        code = "\n".join(ast.unparse(n) for n in code_nodes)
        assert "graph" not in code.lower(), (
            "the record guard must never name the graph in CODE — that coupling is "
            "what blocked the record arm on a graph fact"
        )
        # and the run must call the RECORD half here, not a both-sides guard
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        called = {
            c.func.id
            for c in ast.walk(run)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        }
        assert "require_record_measured" in called, called
        assert "require_seeded" not in called, (
            "`require_seeded` asserts BOTH counts; called inside the record block it "
            "re-introduces the graph-blocks-the-record-arm bug"
        )

    def test_no_GRAPH_fact_can_decide_the_RECORD_block(self):
        """⛔ Structural, because the shipped bug was structural.

        `require_seeded(graph_vectors, record_vectors)` raised `graph_unseeded`
        from INSIDE the record block's `try/except`, which caught it as
        `record_blocked`: a zero-vector graph then blocked the `record_dense` arm
        under a message about "every arm", and the headline comparison went
        UNMEASURED (`#13745` review round 2, finding 1). The pure-function pin
        above cannot see that shape, so this one reads the RUN: inside the record
        block, `graph_vectors` may be handed to `coverage(...)` as the DENOMINATOR
        and nothing else, and no refusal there may be spelled `graph_*`.
        """
        tree = ast.parse(TOOL.read_text())
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        block = next(
            n
            for n in ast.walk(run)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Compare)
            and "record_blocked" in ast.unparse(n.test)
        )
        # every COMPARISON touched inside the block is a decision; none may be about the graph
        decisions = [
            ast.unparse(node)
            for node in ast.walk(block)
            if isinstance(node, ast.Compare) and "graph" in ast.unparse(node).lower()
        ]
        assert not decisions, (
            "a graph comparison decides the record block: "
            f"{decisions} — that is the shape that let a graph fact block the record arm"
        )
        # and no `graph_*` refusal code may be raised there
        codes = [
            node.args[0].value
            for node in ast.walk(block)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Refused"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]
        assert not [c for c in codes if isinstance(c, str) and c.startswith("graph")], codes

    def test_seeded_guard_refuses_an_empty_record(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_record_measured(0)
        assert ei.value.code == "record_unseeded"
        assert "13210" in ei.value.message  # the backfill is named, not implied

    def test_seeded_guard_refuses_an_unmeasurable_record(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_record_measured(None)
        assert ei.value.code == "record_unmeasurable"

    def test_seeded_guard_passes_a_seeded_record(self, tool_mod):
        tool_mod.require_record_measured(800)  # no raise

    def test_a_required_embedder_that_cannot_load_is_a_NAMED_refusal(self, tool_mod, monkeypatch):
        """`EmbeddingModel.get()` RAISES when REQUIRED is armed — catch it by name.

        ⛔ Measured pre-fix: with `TORTOISE_EMBEDDING_MODEL_REQUIRED` truthy and a
        load failure, `get()` raises `EmbedderUnavailableError` (#4861) — its
        whole point on a request path — and `main()` catches only `Refused`, so
        the operator got a traceback and exit 1 where the artifact promises
        `REFUSED[...]` and exit 2. Same shape as the `record_dsn` finding, one
        expression earlier.
        """
        from tortoise.embeddings import EmbeddingModel

        class _Boom(RuntimeError):
            pass

        def _raise(cls, *a, **k):
            raise _Boom("required but unreachable")

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(_raise))
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_embedder()
        assert ei.value.code == "embedder_required_unavailable"

    def test_the_population_guard_refuses_a_cohort_the_record_does_not_hold(self, tool_mod):
        """The label-wide count can be VERIFIED over a population in neither store.

        786 graph vectors and 800 record vectors is `verified=True` for the
        `point` label — and if the record holds NONE of this population's ids,
        `record_dense` ranks nothing and publishes `recall@3 = 0.0` as a finding
        about the leg. That is the `0 >= 0` trap one level deeper.
        """
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_population_in_record(0, 786)
        assert ei.value.code == "record_population_absent"
        # ⛔ THE REPAIR IS NAMED, NOT IMPLIED — and since #13830 the repair is the
        # record's own FOLD, not a backfill this instrument never had: the
        # population IS written through the journaled door, so a shortfall is a
        # fold/ordering fact and `record_runner plan` + `apply --acknowledge-plan`
        # are what heal it (two commands: `apply` writes nothing without the count
        # `plan` prints, and both verbs require --org-id/--graph-id).
        # …and it names the RUNNABLE pair, not one command `apply` would refuse
        # for want of `--org-id`/`--graph-id`/`--acknowledge-plan` (review round 2).
        assert "record_runner.py plan" in ei.value.message, ei.value.message
        assert "--acknowledge-plan" in ei.value.message, ei.value.message
        assert "seed.fold" in ei.value.message, ei.value.message

    def test_the_population_guard_refuses_a_PARTIAL_cohort(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_population_in_record(785, 786)
        assert ei.value.code == "record_population_absent"

    def test_the_population_guard_refuses_an_unmeasurable_membership(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_population_in_record(None, 786)
        assert ei.value.code == "record_population_unmeasurable"

    def test_the_population_guard_passes_a_seeded_population(self, tool_mod):
        tool_mod.require_population_in_record(786, 786)  # no raise

    def test_unverified_coverage_is_refused(self, tool_mod):
        class V:
            verified = False
            reason = "the record holds 0 vector(s) for `point`"

        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_coverage(V())
        assert ei.value.code == "coverage_unverified"

    def test_main_refuses_a_non_positive_population_without_a_traceback(self):
        """⛔ `--population 0` was the one input never validated.

        It raised `ValueError: n must be positive` out of `detectable_effect_pp`
        — an uncaught traceback with exit 1 in the ONLY mode this box can run.

        ⚠️ In the MEASUREMENT path that also means the run died BEFORE an
        artifact existed (`detectable_effect_pp(preregistered_n, …)` and
        `preregistration(preregistered_n)` both raise, and `main()` catches only
        `Refused`), so the `population_mismatch` note never shipped. The related
        `None`-bar fallback in `paired`/`gated_bar_pp` is real at FUNCTION level
        and unreachable in an emitted artifact — it is not what this row pins.
        """
        for bad in ("0", "-5"):
            proc = subprocess.run(
                [sys.executable, str(TOOL), "--preregister", "--population", bad],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 4, (bad, proc.returncode, proc.stderr[-200:])
            assert "must be >= 1" in proc.stderr, proc.stderr[-200:]
            assert "Traceback" not in proc.stderr, proc.stderr[-400:]

    def test_main_refuses_a_k_set_without_the_primary_width(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--ks", "1,5"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 4
        assert "primary k=3" in proc.stderr

    def test_main_refuses_a_depth_below_the_widest_k(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--deep", "3"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 4
        assert "found@deep" in proc.stderr


# ── 5. the record's membership of the population (id-scoped, not label-wide) ─


class _FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def close(self):
        pass


class TestRecordMembership:
    """The population's OWN ids must be in the record — the whole finding.

    ⛔ `record_vector.coverage` answers a LABEL-WIDE question by design, so it
    cannot see this. These cases red the mutants that make the guard label-wide
    or that turn an unreadable record into an empty one.
    """

    SCOPE = types.SimpleNamespace(org_id="org-1", graph_id="graph-1")

    def _ids(self, n: int) -> list[str]:
        return [f"pt-12186{i:06d}" for i in range(n)]

    def test_the_query_is_id_scoped_and_not_a_label_count(self, tool_mod, monkeypatch):
        cur = _FakeCursor((786,))
        monkeypatch.setattr("tortoise.record_serve.open_record", lambda *a, **k: _FakeConn(cur))
        monkeypatch.setattr("tortoise.record_projection.scope_connection", lambda conn, org_id: cur)
        ids = self._ids(786)
        got = tool_mod.record_population_vectors(ids, self.SCOPE, "postgresql://u@h/db")
        assert got == 786
        sql, params = cur.executed[-1]
        assert "= ANY(%s)" in sql, sql  # the ID SET is the predicate
        assert "count(*)" in sql
        assert "embedding_vec IS NOT NULL" in sql  # the column the SERVE slices
        assert params == ("org-1", "graph-1", ids)

    def test_an_unreadable_record_is_unmeasurable_not_empty(self, tool_mod, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no route to the record")

        monkeypatch.setattr("tortoise.record_serve.open_record", boom)
        assert tool_mod.record_population_vectors(self._ids(3), self.SCOPE, "dsn") is None

    def test_no_ids_is_not_a_verdict(self, tool_mod):
        assert tool_mod.record_population_vectors([], self.SCOPE, "dsn") is None

    def test_the_run_asks_the_id_scoped_question_inside_the_arm_loop(self):
        """The guard is WIRED over the population's own ids, and gates ONE arm.

        ⛔ A guard nothing calls is the shape this finding started as: the
        label-wide gate verified, and no assertion anywhere asked the second
        question. ⛔ AND A GUARD THAT GATES THE RUN IS THE OPPOSITE FAILURE
        (`#13734` review round 2): the population's ids reach the GRAPH only
        (`_seed_population` -> the apply-fold, no journal record), so a run-level
        refusal is unsatisfiable and blocks the two arms that need no record
        cohort. It must sit INSIDE the arms loop, where only `record_dense`
        consults it.
        """
        tree = ast.parse(TOOL.read_text())
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        calls = [n for n in ast.walk(run) if isinstance(n, ast.Call)]
        named = [c for c in calls if isinstance(c.func, ast.Name)]
        membership = [c for c in named if c.func.id == "record_population_vectors"]
        assert membership, "run_measurement never asks the record for the population's ids"
        first = membership[0].args[0]
        if isinstance(first, ast.Name):
            # Bound to a local first (the run also needs its length) — follow it.
            bound = [
                n
                for n in ast.walk(run)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == first.id for t in n.targets)
            ]
            assert bound, f"{first.id} is never assigned inside run_measurement"
            first = bound[0].value
        assert isinstance(first, ast.ListComp), ast.dump(first)[:120]
        assert isinstance(first.generators[0].iter, ast.Name)
        assert first.generators[0].iter.id == "population"
        arms_loop = [
            n
            for n in ast.walk(run)
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "ARMS"
        ]
        assert arms_loop
        # …it must be INSIDE the arm loop (gating `record_dense`), not before it.
        guard = [
            c
            for c in ast.walk(arms_loop[0])
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "require_population_in_record"
        ]
        assert guard, (
            "the id-scoped guard must gate the record_dense arm INSIDE the arms "
            "loop — before the loop it blocks the lexical and graph arms too, "
            "and the population's ids never reach the record, so it can never pass"
        )
        # ⛔ AND ITS SECOND ARGUMENT MUST BE THE POPULATION'S OWN LENGTH. A pin
        # that only asserts the call EXISTS is satisfied by
        # `require_population_in_record(population_vectors, 0)`, which makes the
        # guard a no-op — the exact fail-open class this file exists to close.
        n_arg = guard[0].args[1]
        assert isinstance(n_arg, ast.Call), ast.dump(n_arg)[:120]
        assert isinstance(n_arg.func, ast.Name) and n_arg.func.id == "len", ast.dump(n_arg)[:120]
        assert isinstance(n_arg.args[0], ast.Name), ast.dump(n_arg)[:120]
        assert n_arg.args[0].id == "population_ids", (
            "the guard's population size must be len(population_ids) — the same "
            "list it asked the record about, never a constant"
        )
        # ⛔ AND ARG 1 MUST BE THE MEASUREMENT, NOT A LITERAL. Pinning only arg 2
        # let `require_population_in_record(786, len(population_ids))` — a guard
        # made into a no-op — satisfy every assertion above, and let
        # `record_population_vectors(queries, …)` answer about a different set.
        matched_arg = guard[0].args[0]
        assert isinstance(matched_arg, ast.Name), ast.dump(matched_arg)[:120]
        assert matched_arg.id == "population_vectors", (
            "the guard must be handed the record's own count for this population, never a constant"
        )
        bound = [
            n
            for n in ast.walk(run)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "population_vectors" for t in n.targets)
        ]
        assert bound, "population_vectors is never bound in run_measurement"
        value = bound[0].value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name), ast.dump(value)[
            :120
        ]
        assert value.func.id == "record_population_vectors", ast.dump(value)[:120]
        asked = value.args[0]
        assert isinstance(asked, ast.Name) and asked.id == "population_ids", (
            "the count must be asked about population_ids — the same list arg 2 "
            "measures over, or the guard is answering about a different set"
        )
        # ⛔ AND IT MUST BE ENCLOSED IN `if arm == ARM_RECORD_DENSE`. Pinning only
        # the call's POSITION inside the loop left a surviving mutant: hoisting the
        # guard to the top of the loop body makes it fire for `lexical` too — the
        # round-2 failure shape, with the suite green.
        parents = {}
        for node in ast.walk(run):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        node = guard[0]
        enclosures = []
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If):
                enclosures.append(node)
            if node is arms_loop[0]:
                break
        assert enclosures, "the id guard is not inside any `if` in the arms loop"

        def _arm_eq(test: ast.AST) -> bool:
            return (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "arm"
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.comparators[0], ast.Name)
                # the source spells it as the module CONSTANT, so accept both
                # spellings rather than pinning a rename as a failure.
                and test.comparators[0].id in {"ARM_RECORD_DENSE", "record_dense"}
            )

        assert any(_arm_eq(e.test) for e in enclosures), (
            "the id guard must be gated on `arm == ARM_RECORD_DENSE`; ungated in "
            "the loop body it also blocks the lexical and graph arms, which need "
            "no record cohort at all"
        )


# ── 5b. the two mechanisms the arms REST on (a mutation survivor is a hole) ──


class TestTheArmsConstruction:
    """The pure helpers above are fed synthetic traces; these pin the CALLERS.

    ⛔ Measured pre-fix: deleting `"embedding": list(vec)` from `_seed_population`,
    and flipping `if arm != ARM_LEXICAL:` to `if True:` (making the lexical arm
    silently the hybrid arm), each left the whole suite GREEN. Both are the
    difference between "three single-variable arms" and "three reads of the
    same arm", so the two assertions whose absence mattered most were missing.
    """

    def test_seed_population_writes_the_write_paths_own_vector(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ THE SEEDED VECTOR MUST BE THE ONE PRODUCTION WRITES.

        Every write-side embedder in this tree goes through
        `embeddings.compute_embeddings`: it truncates to `EMBED_WORD_BUDGET`
        (512) words before encoding and returns `vec.tolist()`. Pre-fix,
        the seed called `model.encode(content)` and handed the store
        `list(vec)` — untruncated text, and a list of `numpy.float32` scalars,
        a value type no production write produces. Both are pinned here through
        the REAL composition (only the model is doubled), so a revert reds on
        either half: 600 words must reach the encoder as 512, and the payload
        value must be the encoder's `tolist()` (Python floats).

        ⛔ AND IT IS READ OFF THE JOURNAL RECORD (#13830), not off a store call:
        the payload that carries the vector is the one the CHOKEPOINT appends,
        which is also the one the record projects and the graph folds. A test
        that observed a direct `_upsert_point_props` call would pin a path this
        tool no longer has.
        """
        import numpy as np

        from tortoise import embeddings

        seen = {}

        class _Model:
            def encode(self, texts):
                seen["texts"] = list(texts)
                return np.array([[0.1, 0.2, 0.3] for _ in texts], dtype=np.float32)

        monkeypatch.setattr(
            embeddings.EmbeddingModel, "get", classmethod(lambda cls, *a, **k: _Model())
        )
        log = _journal_double(monkeypatch)
        folded = _FoldDouble()

        long_content = " ".join(f"w{i}" for i in range(600))
        population = [{"id": "pt-12186000000", "content": long_content}]
        seed = tool_mod._seed_population(folded, population, journal_path=tmp_path / "j.jsonl")

        assert seed.written == 1
        assert len(seen["texts"][0].split()) == embeddings.EMBED_WORD_BUDGET, (
            "the seed encoded the UNTRUNCATED text — a composition no production write makes"
        )
        assert len(log.events) == 1, log.events
        point = log.events[0]["point"]
        assert log.events[0]["type"] == "PointAdded", log.events[0]
        embedding = point["embedding"]
        assert embedding == pytest.approx([0.1, 0.2, 0.3]), embedding
        assert all(type(v) is float for v in embedding), (
            f"the payload carries {[type(v).__name__ for v in embedding]} — `list(numpy_row)` "
            "is not the value type `compute_embeddings` returns"
        )
        assert point["content"] == long_content
        # ⛔ THE GRAPH IS WRITTEN BY THE FOLD OF THE SAME RECORD, not by a second
        # write: the fold double sees the very event that was appended.
        assert folded.events == log.events, (
            "the graph fold did not receive the journal record's own payload — the "
            "two derived stores would then be fed by different values"
        )

    def test_the_seed_projects_each_row_into_the_record_through_the_LIVE_door(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ THE RECORD HALF, ASSERTED WHERE IT HAPPENS (#13830).

        The whole issue is that the population reached the graph and never the
        record. So the seed must reach the record through
        `record_writer.write_live_record` — the LIVE door the chokepoint calls —
        with the journal's OWN proven position as `seq`. Pinned on the seam's
        arguments rather than on a swallowed return, because a projection that
        was never CALLED and one that was called and refused are different facts
        and only the first is this test's subject.
        """
        from tortoise import record_writer

        log = _journal_double(monkeypatch)
        calls = []
        applied = types.SimpleNamespace(applied=True, refusal=None, outcome="applied")

        def _live(record, **kwargs):
            calls.append({"record": record, **kwargs})
            return applied

        monkeypatch.setattr(record_writer, "write_live_record", _live)
        population = [
            {"id": "pt-12186000000", "content": "alpha"},
            {"id": "pt-12186000001", "content": "beta"},
        ]
        seed = tool_mod._seed_population(
            _FoldDouble(),
            population,
            journal_path=tmp_path / "j.jsonl",
            tenant=("org-1", "graph-1"),
            encode_many=lambda texts: [[0.5, 0.25] for _ in texts],
        )
        assert [c["seq"] for c in calls] == log.positions, (
            "the record was not handed the journal's OWN positions — a replayed "
            "journal and a live write would number the same record differently"
        )
        assert all(c["tenant"] == ("org-1", "graph-1") for c in calls), calls
        assert all(c["durable"] is True for c in calls), calls
        assert [c["record"] for c in calls] == log.events
        assert seed.record_applied == 2 and seed.record_unprojected == 0, seed.as_dict()
        assert seed.first_seq == 1 and seed.last_seq == 2, seed.as_dict()

    def test_seed_population_refuses_a_row_it_could_not_embed(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """A `None` vector is a NAMED refusal, never a written hole."""
        log = _journal_double(monkeypatch)
        folded = _FoldDouble()
        population = [
            {"id": "pt-12186000000", "content": "alpha"},
            {"id": "pt-12186000001", "content": "beta"},
        ]
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod._seed_population(
                _FoldDouble(),
                population,
                journal_path=tmp_path / "j.jsonl",
                encode_many=lambda texts: [[0.1], None],
            )
        assert ei.value.code == "seed_vectors_missing"
        assert "pt-12186000001" in ei.value.message, ei.value.message
        assert log.events == [], "a partially-embedded population must not be journaled"
        assert folded.events == []

        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod._seed_population(
                _FoldDouble(),
                population,
                journal_path=tmp_path / "j.jsonl",
                encode_many=lambda texts: [[0.1]],
            )
        assert ei.value.code == "seed_vectors_missing"
        assert log.events == []

    def test_the_seed_carries_no_direct_write_of_either_store(self):
        """⛔ THE ANTI-SECOND-WRITER PIN, AS CODE RATHER THAN AS PROSE (#13830).

        The defect this fix removes was a DIRECT projection write
        (`_upsert_point_props`) that emitted no journal record; the tempting
        repair is a direct record write as well. Both are banned here: the seed
        body must not call the fold's upsert directly, and must not import the
        record writer's non-live entry points. It reaches both stores the one way
        #13345 built — `live_write.write`.
        """
        tree = ast.parse(TOOL.read_text())
        seed = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_seed_population"
        )
        attrs = {
            n.func.attr
            for n in ast.walk(seed)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "_upsert_point_props" not in attrs, (
            "the seed writes the graph through the projection's apply-fold only — a "
            "direct `_upsert_point_props` is the pre-#13830 path that emitted no "
            "journal record, so the record could never receive the population"
        )
        chokepoint = [
            n
            for n in ast.walk(seed)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "write"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "live_write"
        ]
        assert chokepoint, (
            "the seed no longer calls the chokepoint — `live_write.write` is the ONE "
            "door that appends, projects and folds in the documented order"
        )
        # …and it is handed the projection and the journal, or the fold/append half
        # would silently be a no-op (both are keyword-only on the door).
        passed = {k.arg for c in chokepoint for k in c.keywords}
        assert {"log", "projection"} <= passed, passed
        imported = {
            (a.name or "" for a in n.names) for n in ast.walk(seed) if isinstance(n, ast.ImportFrom)
        }
        flat = set().union(*imported) if imported else set()
        for banned in ("write_live_record", "write_records", "RecordWriter"):
            assert banned not in flat, (
                f"the seed imports `{banned}` directly — the record is fed by the "
                f"chokepoint (`live_write.write`), never by a second spelling of "
                f"the write path (#13345)"
            )

    def test_the_lexical_arm_does_not_submit_the_dense_leg(self, tool_mod, monkeypatch):
        """`lexical` must read with NO embedder; the other arms must not patch."""
        from tortoise import embeddings

        seen = []

        class _Sdk:
            def _tortoise_fts_query(self, text, **kwargs):
                seen.append(embeddings.EmbeddingModel.get())
                return []

        monkeypatch.setattr(
            embeddings.EmbeddingModel,
            "get",
            classmethod(lambda cls, *a, **k: "A-MODEL"),
        )

        tool_mod._query(_Sdk(), tool_mod.ARM_LEXICAL, "q", 50, [])
        assert seen == [None], (
            "the lexical arm handed the read an embedder — it would be the hybrid "
            "arm wearing the lexical arm's name"
        )

        for arm in (tool_mod.ARM_GRAPH_DENSE, tool_mod.ARM_RECORD_DENSE):
            seen.clear()
            tool_mod._query(_Sdk(), arm, "q", 50, [])
            assert seen == ["A-MODEL"], (arm, seen)

        # and the patch is restored, not left installed for a later arm
        assert embeddings.EmbeddingModel.get() == "A-MODEL"


# ── 5d. THE JOURNALED DOOR: the declaration, and what the seed reports ─────


class TestTheJournalDeclaration:
    """#13830 — the population enters through the journal, so the journal is stated.

    ⛔ Two spellings of the path is not a preference, it is the failure this class
    refuses: `record_vector.journal_ready` compares the record's positions against
    ``TORTOISE_RECORD_JOURNAL``, so a run that SEEDS one path while the record arm's
    gate MEASURES another would report the tenant as behind (or ahead) on the
    strength of a file the population is not in. `--journal` alone is enough to
    write, which is why `run_measurement` declares it; two DIFFERENT names is an
    operator error and is refused rather than resolved.
    """

    def test_no_journal_is_a_named_refusal(self, tool_mod, monkeypatch):
        from tortoise import record_vector

        monkeypatch.delenv(record_vector.RECORD_JOURNAL_ENV, raising=False)
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_journal("")
        assert ei.value.code == "no_journal", ei.value.code
        # the refusal names the door and the reason, not just the variable
        assert "journaled door" in ei.value.message, ei.value.message

    def test_the_env_declaration_is_used_when_the_flag_is_absent(
        self, tool_mod, monkeypatch, tmp_path
    ):
        from tortoise import record_vector

        journal = tmp_path / "events.jsonl"
        monkeypatch.setenv(record_vector.RECORD_JOURNAL_ENV, str(journal))
        assert tool_mod.require_journal("") == journal

    def test_the_flag_alone_is_used_and_declared(self, tool_mod, monkeypatch, tmp_path):
        from tortoise import record_vector

        monkeypatch.delenv(record_vector.RECORD_JOURNAL_ENV, raising=False)
        journal = tmp_path / "events.jsonl"
        assert tool_mod.require_journal(str(journal)) == journal

    def test_two_DIFFERENT_declarations_are_refused_not_resolved(
        self, tool_mod, monkeypatch, tmp_path
    ):
        from tortoise import record_vector

        monkeypatch.setenv(record_vector.RECORD_JOURNAL_ENV, str(tmp_path / "a.jsonl"))
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_journal(str(tmp_path / "b.jsonl"))
        assert ei.value.code == "journal_conflict", ei.value.code
        assert "a.jsonl" in ei.value.message and "b.jsonl" in ei.value.message, ei.value.message

    def test_the_same_path_twice_is_not_a_conflict(self, tool_mod, monkeypatch, tmp_path):
        from tortoise import record_vector

        journal = tmp_path / "events.jsonl"
        monkeypatch.setenv(record_vector.RECORD_JOURNAL_ENV, str(journal))
        assert tool_mod.require_journal(str(journal)) == journal

    def test_main_restores_the_declaration_it_made(self, tool_mod, monkeypatch):
        """⛔ The declaration is the RUN's, not the process's (#13830).

        `run_measurement` sets `TORTOISE_RECORD_JOURNAL` so the record arm's
        readiness gate measures the journal this run seeded, and `main()` puts the
        caller's value back. ⚠️ The restore lives in `main()`, so it covers a
        `main()` caller — NOT one that calls `run_measurement` directly (that
        caller inherits the declaration, and the comment in `main()` says so).
        """
        from tortoise import record_vector

        monkeypatch.delenv(record_vector.RECORD_JOURNAL_ENV, raising=False)

        def _run(_args):
            os.environ[record_vector.RECORD_JOURNAL_ENV] = "/tmp/seeded.jsonl"
            return 0

        monkeypatch.setattr(tool_mod, "run_measurement", _run)
        assert tool_mod.main(["--population", str(M2_N)]) == 0
        assert record_vector.RECORD_JOURNAL_ENV not in os.environ

    def test_a_prior_declaration_is_put_BACK_unchanged(self, tool_mod, monkeypatch):
        from tortoise import record_vector

        monkeypatch.setenv(record_vector.RECORD_JOURNAL_ENV, "/tmp/original.jsonl")

        def _run(_args):
            os.environ[record_vector.RECORD_JOURNAL_ENV] = "/tmp/seeded.jsonl"
            return 3

        monkeypatch.setattr(tool_mod, "run_measurement", _run)
        assert tool_mod.main(["--population", str(M2_N)]) == 3
        assert os.environ[record_vector.RECORD_JOURNAL_ENV] == "/tmp/original.jsonl"


class TestTheDSNDeclaration:
    """#13839 review finding 1 — `--dsn` must arm the WRITE, not only the reads.

    ⛔ THE FAILURE THIS CLASS PINS. `live_write.write` asks the record seam's own
    arming predicate, and that predicate reads ``TORTOISE_RECORD_DSN`` from the
    ENVIRONMENT with NO argument (``record_writer._live_write_verdict`` ->
    ``record_dsn()``, tortoise/record_writer.py:2754-2759). A run that resolved its
    DSN from the ``--dsn`` FLAG therefore armed every read it made
    (``record_vector.coverage(record_dsn=dsn)``, ``record_population_vectors(...,
    dsn)``) and left the write seam UNARMED: the seed wrote nothing, ``_fold_needed``
    was false so no repair was emitted, and the artifact published
    ``record_armed: false`` for a store the same process could query.

    ⛔ THE ASSERTION IS AT THE MOMENT BEFORE THE SEED, not on the artifact: the
    declaration has to be in place when ``_seed_population`` runs, and a test that
    read the artifact could not tell "declared" from "declared too late".
    """

    DSN = "postgresql://user@record.invalid:5432/tortoise"

    def _reach_the_declaration(self, tool_mod, monkeypatch, tmp_path, *, prior=None):
        """Run `main()` up to the first corpus read, capturing the declarations."""
        import tools.window_quality_m2 as wqm
        from tortoise import record_vector
        from tortoise.record_writer import RECORD_DSN_ENV

        if prior is None:
            monkeypatch.delenv(RECORD_DSN_ENV, raising=False)
        else:
            monkeypatch.setenv(RECORD_DSN_ENV, prior)
        monkeypatch.delenv(record_vector.RECORD_JOURNAL_ENV, raising=False)

        captured: dict[str, str | None] = {}

        def _stop(_sessions, _want):
            # ⛔ THE FIRST LINE PAST BOTH DECLARATIONS — the moment the seed would
            # have read the environment the write seam reads.
            captured["dsn"] = os.environ.get(RECORD_DSN_ENV)
            captured["journal"] = os.environ.get(record_vector.RECORD_JOURNAL_ENV)
            raise tool_mod.Refused("test_stop_here", "the run reached the point under test")

        monkeypatch.setattr(wqm, "harvest_turn_texts", _stop)
        monkeypatch.setattr(tool_mod, "require_corpus", lambda _sessions: [])
        monkeypatch.setattr(tool_mod, "require_embedder", lambda: None)
        # the real `require_record` opens a store; this test is about the flag's
        # RESOLVED VALUE reaching the write seam, not about reachability.
        monkeypatch.setattr(tool_mod, "require_record", lambda dsn: dsn)
        monkeypatch.setattr(
            record_vector,
            "_resolve_scope",
            lambda: types.SimpleNamespace(org_id="org-1", graph_id="graph-1"),
        )
        rc = tool_mod.main(
            [
                "--population",
                str(M2_N),
                "--dsn",
                self.DSN,
                "--journal",
                str(tmp_path / "events.jsonl"),
            ]
        )
        return rc, captured, RECORD_DSN_ENV

    def test_a_flag_DSN_is_declared_for_the_write_seam(self, tool_mod, monkeypatch, tmp_path):
        rc, captured, dsn_env = self._reach_the_declaration(tool_mod, monkeypatch, tmp_path)
        assert rc == 2, f"the stop refusal became exit {rc} — the run did not reach the seed"
        assert captured["dsn"] == self.DSN, (
            "the resolved --dsn was NOT declared into "
            f"{dsn_env} before the seed — the write seam's `armed` predicate reads "
            "only the environment, so `live_write.write` would answer 'unarmed' "
            "while every read in this run uses the flag's store: the seed becomes "
            "a silent no-op, no fold is emitted, and the artifact publishes "
            "record_armed=false for a record it can read"
        )
        assert captured["journal"] == str(tmp_path / "events.jsonl"), captured

    def test_a_prior_DSN_declaration_is_put_BACK_unchanged(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ The declaration is the RUN's, not the process's.

        `main()` restores the caller's value — a library caller whose own record is
        armed must get that arming back after a run, exactly as it does for the
        journal path.
        """
        prior = "postgresql://someone-else@other.invalid:5432/tortoise"
        rc, captured, dsn_env = self._reach_the_declaration(
            tool_mod, monkeypatch, tmp_path, prior=prior
        )
        assert rc == 2, rc
        assert captured["dsn"] == self.DSN, (
            "the flag must WIN inside the run (an explicit --dsn beats the env)"
        )
        assert os.environ.get(dsn_env) == prior, (
            f"{dsn_env} was left as this run's value — a later caller would measure "
            "against a record it never named"
        )

    def test_the_seam_that_answers_arming_reads_the_environment(self, tool_mod, monkeypatch):
        """The premise of the two tests above, asserted rather than assumed.

        If `write_live_record` ever grew a DSN argument, declaring the environment
        would stop being the fix and this class would be pinning a no-op.
        """
        from tortoise import record_writer

        monkeypatch.delenv(record_writer.RECORD_DSN_ENV, raising=False)
        assert record_writer.record_dsn() is None
        verdict = record_writer._live_write_verdict(durable=True)
        assert verdict[0] == "unarmed", verdict
        monkeypatch.setenv(record_writer.RECORD_DSN_ENV, self.DSN)
        assert record_writer.record_dsn() == self.DSN
        assert record_writer._live_write_verdict(durable=True)[0] != "unarmed"


class TestTheSeedReport:
    """What the seeding pass reports, and the repair it names when it must."""

    def _report(self, tool_mod, **over):
        base = dict(
            journal="/tmp/j.jsonl",
            org_id="org-1",
            graph_id="graph-1",
            written=2,
            record_armed=True,
            record_applied=2,
            record_unprojected=0,
            positions_unproven=0,
            folded=2,
            first_seq=1,
            last_seq=2,
        )
        base.update(over)
        return tool_mod.SeedReport(**base)

    def test_a_clean_seed_names_no_repair(self, tool_mod):
        payload = self._report(tool_mod).as_dict()
        assert payload["record_applied"] == 2 and payload["record_unprojected"] == 0
        assert "fold" not in payload, (
            "a clean seed must not hand the operator a repair — a warning "
            "that always fires is a warning nobody reads"
        )

    def test_the_repair_is_the_RUNNABLE_two_step_fold(self, tool_mod):
        """⛔ A REPAIR COMMAND ARGPARSE REJECTS IS NOT A REPAIR (review round 2).

        The first cut emitted `... apply --journal <path>` alone. `record_runner`
        requires `--org-id`/`--graph-id` on every verb, and `apply` refuses a
        WRITING run without `--acknowledge-plan <N>` — a number only `plan` prints
        (#11469). Measured: the bare command exits 2 with
        `the following arguments are required: --org-id, --graph-id`. So the
        artifact must carry BOTH commands, with this run's tenant filled in.
        """
        payload = self._report(tool_mod, record_applied=1, record_unprojected=1).as_dict()
        fold = payload["fold"]
        assert fold["plan"].startswith(
            "uv run python tools/record_runner.py plan --from-checkpoint --journal /tmp/j.jsonl"
        ), fold
        assert "--org-id org-1 --graph-id graph-1" in fold["plan"], fold["plan"]
        assert "--acknowledge-plan" in fold["apply"], fold["apply"]
        assert "--org-id org-1 --graph-id graph-1" in fold["apply"], fold["apply"]
        # ⛔ AND BOTH NAME THE STORE (#13839 finding 1, corollary): `record_runner`
        # resolves the DSN from the ENVIRONMENT, so a paste from a run whose DSN came
        # from `--dsn` folds into whatever `TORTOISE_RECORD_DSN` happens to name —
        # silently, when it names a different record. A shell variable, never the
        # value, so no secret enters the artifact.
        for verb in ("plan", "apply"):
            assert '--dsn "$TORTOISE_RECORD_DSN"' in fold[verb], fold[verb]
        assert "plan" in fold["why"] and "acknowledge" in fold["why"], fold["why"]
        assert "unprojected" in payload["note"], payload["note"]

    def test_the_emitted_commands_PARSE_against_the_real_record_runner(self, tool_mod):
        """⛔ THE PIN THAT WOULD HAVE CAUGHT ROUND 2's FINDING.

        The first cut emitted a command `record_runner` REJECTS: `apply` requires
        `--org-id`/`--graph-id` on every verb and refuses a writing run without
        `--acknowledge-plan <N>` (a number only `plan` prints). A prefix assertion
        on the string could not see that, so this parses both emitted commands with
        the runner's OWN parser — the same object that refuses them at run time.
        """
        # ⛔ REGISTERED BEFORE exec_module, because `record_runner` declares
        # `@dataclass`es and `dataclasses` resolves `from __future__ import
        # annotations` strings through `sys.modules[cls.__module__]`. The tool under
        # test cannot use a dataclass for the same reason (see `SeedReport`'s own
        # note); a module this TEST loads is free to be registered properly.
        name = "record_runner_for_13830"
        spec = importlib.util.spec_from_file_location(
            name, REPO_ROOT / "tools" / "record_runner.py"
        )
        runner = importlib.util.module_from_spec(spec)
        sys.modules[name] = runner
        try:
            spec.loader.exec_module(runner)
            parser = runner._parser()
        finally:
            sys.modules.pop(name, None)
        payload = self._report(tool_mod, record_applied=1, record_unprojected=1).as_dict()

        def _argv(command: str) -> list[str]:
            """`uv run python tools/record_runner.py …` -> the args the parser reads."""
            tokens = shlex.split(command)
            assert tokens[:3] == ["uv", "run", "python"], tokens[:3]
            assert tokens[3].endswith("tools/record_runner.py"), tokens[3]
            return tokens[4:]

        plan = parser.parse_args(_argv(payload["fold"]["plan"]))
        assert plan.verb == "plan", plan
        assert (plan.org_id, plan.graph_id) == ("org-1", "graph-1"), plan
        # ⛔ THE REPAIR MUST NAME THE STORE THIS RUN MEASURED (#13839 finding 1).
        # Absent, `record_runner` falls back to the environment: an obvious refusal
        # when it is unset, a SILENT fold into the wrong record when it names a
        # different one — 786 population rows under a tenant nobody measured.
        assert plan.dsn == "$TORTOISE_RECORD_DSN", plan.dsn
        assert plan.from_checkpoint is True, (
            "without --from-checkpoint the plan reports the WHOLE journal, which is a "
            "different number from the one `apply` then wants"
        )
        applied = parser.parse_args(
            _argv(payload["fold"]["apply"].replace("<N: the count `plan` reports>", "3"))
        )
        assert applied.verb == "apply", applied
        assert applied.acknowledge_plan == 3, applied
        assert (applied.org_id, applied.graph_id) == ("org-1", "graph-1"), applied
        assert applied.dsn == "$TORTOISE_RECORD_DSN", applied.dsn

    def test_no_tenant_is_a_VISIBLE_placeholder_never_a_guessed_id(self, tool_mod):
        """⛔ The `--org-id`/`--graph-id` branch WITH no tenant is REACHABLE.

        An ARMED record with an unresolved tenant is a real state: the seam refuses
        the write `no_tenant`, that refusal sets `unprojected=1`, and this report
        then has no ids to fill in. Emitting `<org-id>`/`<graph-id>` says "supply
        this"; dropping the flags would emit a command argparse rejects, and
        guessing an id would be the mis-scoped write `#9192` forbids.
        """
        payload = self._report(
            tool_mod, record_applied=0, record_unprojected=1, org_id=None, graph_id=None
        ).as_dict()
        fold = payload["fold"]
        assert "--org-id <org-id>" in fold["plan"], fold["plan"]
        assert "--org-id <org-id>" in fold["apply"], fold["apply"]
        assert payload["org_id"] is None and payload["graph_id"] is None, payload

    def test_an_unproven_position_on_an_ARMED_record_names_the_fold(self, tool_mod):
        """A record row with no journal_event row is UNPROJECTED to the read gate."""
        payload = self._report(
            tool_mod, positions_unproven=2, first_seq=None, last_seq=None
        ).as_dict()
        assert "fold" in payload, payload
        assert payload["positions_unproven"] == 2, payload

    def test_an_unproven_position_on_an_unarmed_record_needs_NO_fold(self, tool_mod):
        """⛔ Nothing was written to a record, so there is no fold to run.

        A journal append that cannot prove its position on an UNARMED deployment
        is a fact about the append; the record never saw the write, so a repair
        aimed at the record would send the operator to a store this run did not
        touch.
        """
        payload = self._report(
            tool_mod, record_armed=False, record_applied=0, positions_unproven=2
        ).as_dict()
        assert "fold" not in payload, payload

    def test_an_UNARMED_record_is_not_read_as_a_refusal(self, tool_mod):
        """⛔ `record_applied == 0` MEANS TWO THINGS, AND THE REPORT KEEPS THEM APART.

        A deployment with no record has nothing to apply to (0 applied, and that
        is not a fault); an ARMED record that refused the projection has 0 applied
        and IS one. Collapsing them into one zero is how "no record in this run"
        gets read as "the record refused the population" — the same class of
        misreading `LiveWrite.armed`'s third state exists to prevent.
        """
        unarmed = self._report(tool_mod, record_armed=False, record_applied=0).as_dict()
        assert unarmed["record_armed"] is False and unarmed["record_applied"] == 0
        assert "fold" not in unarmed, (
            "an unarmed record is not an unprojected one — nothing was attempted, "
            "so there is no fold to run"
        )
        measured = self._report(tool_mod, record_armed=None, record_applied=0).as_dict()
        assert measured["record_armed"] is None, (
            "a projection that RAISED leaves arming UNMEASURED, never False"
        )

    def test_the_report_states_the_door_it_used(self, tool_mod):
        payload = self._report(tool_mod).as_dict()
        assert "live_write.write" in payload["door"], payload["door"]

    def test_the_repair_NAMES_the_store_it_must_fold_into(self, tool_mod):
        """⛔ THE `--dsn` VARIABLE IS ONLY HALF OF NAMING THE STORE (#13839).

        `--dsn "$TORTOISE_RECORD_DSN"` says WHICH variable, never WHICH value: a
        shell exporting a DIFFERENT record folds this population into a store nobody
        measured, silently. The artifact therefore carries the DSN's FINGERPRINT —
        the tree's own secret-hygiene handle (#2903/#2796) — so the operator can tell
        whether their shell is the record this run measured. Asserted as a PAIR, in
        the artifact and in the fold block, because a fingerprint that is published
        but not beside the command is not a comparison.
        """
        from tortoise.record_writer import dsn_fingerprint

        dsn = "postgresql://u:sup3rsecret@h:5432/db"
        expected = dsn_fingerprint(dsn)
        payload = self._report(
            tool_mod,
            record_applied=1,
            record_unprojected=1,
            record_dsn_fingerprint=expected,
        ).as_dict()
        fold = payload["fold"]
        assert payload["record_dsn_fingerprint"] == expected, payload
        assert fold["dsn_fingerprint"] == expected, fold
        assert '--dsn "$TORTOISE_RECORD_DSN"' in fold["plan"], fold["plan"]
        assert '--dsn "$TORTOISE_RECORD_DSN"' in fold["apply"], fold["apply"]
        assert "sup3rsecret" not in json.dumps(payload), (
            "the artifact carries the DSN VALUE — a secret must travel as its "
            "fingerprint, never as itself"
        )

    def test_a_DECLINED_write_is_PUBLISHED_never_a_clean_zero(self, tool_mod):
        """⛔ `applied=False, unprojected=0` IS TWO FACTS (#13839, finding 1).

        `live_write` reports the seam's durable DECLINE with exactly the shape an
        UNARMED deployment has, so a seed the record REFUSED OUTRIGHT used to publish
        as a clean one: 0 applied, 0 unprojected, no repair. It is published instead —
        and it gets NO `fold`, because a decline means there is no durable line for a
        fold to replay; a repair command that cannot work is the same class of false
        promise a rejected-by-argparse command was in round 2.
        """
        payload = self._report(
            tool_mod,
            record_applied=0,
            record_unprojected=0,
            record_declined=2,
            record_armed=True,
        ).as_dict()
        assert payload["record_declined"] == 2, payload
        assert "fold" not in payload, payload.get("fold")
        assert payload["declined"]["count"] == 2, payload["declined"]
        assert "NO FOLD HEALS" in payload["declined"]["why"], payload["declined"]

    def test_a_clean_seed_reports_ZERO_declined(self, tool_mod):
        """The count is always present, so its absence can never read as "unknown"."""
        payload = self._report(tool_mod).as_dict()
        assert payload["record_declined"] == 0, payload
        assert "declined" not in payload, payload


class TestTheDeclinedWrite:
    """#13839 review, finding 1 — the fourth store answer, counted where it happens.

    ⛔ THE SEED CAN BE REFUSED IN A WAY THAT LOOKS LIKE SUCCESS. Every armed
    `applied=False` EXCEPT the durable decline sets `unprojected=1`
    (`live_write.write`), so `no_durable_journal` is the ONE arm that answers
    "the record did not take this row" while the report's two counts both stay 0.
    Left uncounted it is indistinguishable from a seed the record took, which is the
    silent-no-op shape this whole finding is about.
    """

    def test_a_DECLINED_write_is_COUNTED_at_the_seed(self, tool_mod, monkeypatch, tmp_path):
        from tortoise import record_writer

        _journal_double(monkeypatch)
        decline = types.SimpleNamespace(
            applied=False, refusal=record_writer.NON_DURABLE_JOURNAL_REFUSAL, outcome="declined"
        )
        monkeypatch.setattr(record_writer, "write_live_record", lambda record, **_kw: decline)
        population = [
            {"id": "pt-12186000000", "content": "alpha"},
            {"id": "pt-12186000001", "content": "beta"},
        ]
        seed = tool_mod._seed_population(
            _FoldDouble(),
            population,
            journal_path=tmp_path / "j.jsonl",
            encode_many=lambda texts: [[0.5] for _ in texts],
        )
        assert (seed.record_applied, seed.record_unprojected) == (0, 0), seed.as_dict()
        assert seed.record_declined == 2, seed.as_dict()
        payload = seed.as_dict()
        assert payload["record_declined"] == 2 and payload["declined"]["count"] == 2, payload
        assert "fold" not in payload, payload.get("fold")

    def test_an_UNPROJECTED_write_is_NOT_counted_as_declined(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """The two arms are disjoint, or the counts would double-report one write.

        An REFUSED projection carries a `refusal` token too (`write_failed`,
        `no_tenant`, …) — `live_write` marks it `unprojected=1` precisely so it is
        NOT this arm. Collapsing them would count one lost row twice and point the
        operator at the wrong repair (a fold DOES heal an unprojected write).
        """
        from tortoise import record_writer

        _journal_double(monkeypatch)
        refused = types.SimpleNamespace(applied=False, refusal="write_failed", outcome="refused")
        monkeypatch.setattr(record_writer, "write_live_record", lambda record, **_kw: refused)
        seed = tool_mod._seed_population(
            _FoldDouble(),
            [{"id": "pt-12186000000", "content": "alpha"}],
            journal_path=tmp_path / "j.jsonl",
            encode_many=lambda texts: [[0.5] for _ in texts],
        )
        assert seed.record_unprojected == 1, seed.as_dict()
        assert seed.record_declined == 0, seed.as_dict()

    def test_a_CUT_OVER_write_is_NOT_a_position_unproven(self, tool_mod, monkeypatch, tmp_path):
        """⛔ `seq is None` HAS TWO CAUSES AND ONLY ONE IS "unproven" (#14002).

        On a cut-over graph the chokepoint does not append at all — the record owns
        the ordinal — so the outcome has NO journal position BY DESIGN
        (`LiveWrite(appended=False, seq=None)`), while an append that could not
        prove its position is `appended=True, seq=None`. Counting both as unproven
        made a HEALTHY record-only seed publish `positions_unproven`, which gates
        this tool's own report: a clean seed read as a broken one and the reported
        arm was refused on the strength of it.

        ⛔ PRE-FIX THIS LEG DOES NOT EXIST: the whole suite stayed green when the
        discriminator was reverted to `if outcome.seq is None:` — the report-object
        tests construct `SeedReport` DIRECTLY, so they never reach this branch. The
        write path here is the REAL chokepoint (`live_write.write`), not a stub:
        only the record's two seam answers are doubled, so the outcome under test
        is the one production produces.
        """
        from tortoise import record_writer

        log = _journal_double(monkeypatch)
        monkeypatch.setattr(record_writer, "record_owns_position",
                            lambda **_kw: True, raising=True)
        applied = types.SimpleNamespace(applied=True, refusal=None, outcome="applied")
        monkeypatch.setattr(record_writer, "write_live_record",
                            lambda record, **_kw: applied, raising=True)

        seed = tool_mod._seed_population(
            _FoldDouble(),
            [{"id": "pt-12186000000", "content": "alpha"}],
            journal_path=tmp_path / "j.jsonl",
            encode_many=lambda texts: [[0.5] for _ in texts],
        )

        assert seed.positions_unproven == 0, (
            "a cut-over write (the record owns the ordinal, so the append was "
            "skipped) was counted as a position the append could not prove — a "
            f"healthy seed now reports unproven positions: {seed.as_dict()}")
        assert seed.record_applied == 1, seed.as_dict()
        assert log.events == [], (
            "the chokepoint appended to the journal on a graph the record owns — "
            "this leg is only about the classification, so the skip is the premise")
        assert (seed.first_seq, seed.last_seq) == (None, None), (
            "a position appeared from nowhere: the record-only arm has no journal "
            "ordinal to report")

    def test_the_seed_publishes_the_STORE_it_wrote_never_the_secret(
        self, tool_mod, monkeypatch, tmp_path
    ):
        from tortoise import record_writer

        _journal_double(monkeypatch)
        dsn = "postgresql://u:sup3rsecret@h:5432/db"
        applied = types.SimpleNamespace(applied=True, refusal=None, outcome="applied")
        monkeypatch.setattr(record_writer, "write_live_record", lambda record, **_kw: applied)
        seed = tool_mod._seed_population(
            _FoldDouble(),
            [{"id": "pt-12186000000", "content": "alpha"}],
            journal_path=tmp_path / "j.jsonl",
            record_dsn=dsn,
            encode_many=lambda texts: [[0.5] for _ in texts],
        )
        assert seed.record_dsn_fingerprint == record_writer.dsn_fingerprint(dsn), seed.as_dict()
        assert "sup3rsecret" not in json.dumps(seed.as_dict())

    def test_an_unarmed_run_publishes_NO_fingerprint(self, tool_mod, monkeypatch, tmp_path):
        """No record in this run ⇒ `null`, not the fingerprint of ""."""
        _journal_double(monkeypatch)
        seed = tool_mod._seed_population(
            _FoldDouble(),
            [{"id": "pt-12186000000", "content": "alpha"}],
            journal_path=tmp_path / "j.jsonl",
            encode_many=lambda texts: [[0.5] for _ in texts],
        )
        assert seed.record_dsn_fingerprint is None, seed.as_dict()

    def test_the_seed_opens_a_DURABLE_journal(self):
        """⛔ THE CONSTRUCTOR SPELLING IS THE PROPERTY (#13839 review, rule 3).

        `_journal_double` replaces `EventLog` with a lambda whose double hardcodes
        `durable = True`, so the realistic revert — `EventLog.scratch(path)` — reds
        by `AttributeError` on the lambda rather than through any assertion that
        names durability. It is not a cosmetic distinction: a non-durable journal
        makes the record seam DECLINE (`no_durable_journal`), and a decline is
        `applied=False, unprojected=0` — the clean-looking shape `record_declined`
        exists to undo. Pinned in the SOURCE, where the double cannot mask it.
        """
        tree = ast.parse(TOOL.read_text())
        seed = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_seed_population"
        )
        ctors = [
            n
            for n in ast.walk(seed)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "EventLog"
        ]
        assert ctors, (
            "the seed opens no journal through `EventLog(...)` — `EventLog.scratch(...)`"
            " would make the record seam DECLINE every row"
        )
        assert all(len(c.args) == 1 and not c.keywords for c in ctors), [
            ast.dump(c) for c in ctors
        ]
        attrs = {
            n.func.attr
            for n in ast.walk(seed)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "scratch" not in attrs, (
            "the seed constructs a SCRATCH journal — `durable` is then False, the"
            " record seam declines every write, and the seed publishes 0 applied / 0"
            " unprojected, i.e. it reads as clean (#13839 finding 1)"
        )


class TestTheDeferredWrite:
    """#14185 — the FIFTH store answer: a QUEUED fold is not a LOST one.

    With the projection worker armed, `live_write` acknowledges the write and
    queues the record's fold, so the returned outcome is `applied=False` **and**
    `unprojected=1` — truthfully, the record has not taken the line YET. The
    branch that counts these apart (`elif getattr(outcome, "deferred", False)`) is
    what keeps a healthy armed seed from reporting "N write(s) came back
    unprojected" plus a repair command.

    ⛔ WITHOUT THIS LEG THE BRANCH IS UNPROVEN (review cycle 7, ISSUE 3): delete
    the `elif`, or drop `record_deferred` from `SeedReport.as_dict()`, and the
    whole suite stayed green — while the exact defect the branch prevents (a
    HEALTHY armed seed reporting a loss and offering a repair) came back
    silently. The legs build a `SeedReport` directly; only this one reaches the
    counting branch, and it does so through the REAL chokepoint.
    """

    def test_a_DEFERRED_write_is_COUNTED_apart_and_names_NO_repair(
        self, tool_mod, monkeypatch, tmp_path
    ):
        from tortoise import projection_worker, record_writer

        applied = types.SimpleNamespace(applied=True, refusal=None, outcome="applied")
        log = _journal_double(monkeypatch)
        monkeypatch.setattr(record_writer, "write_live_record", lambda record, **_kw: applied)
        # ⛔ BOTH SWITCHES, exactly as `tests/test_14185_projection_worker.py::armed`
        # does it: the LOOP is armed by its flag, and the RECORD by its own DSN
        # (`#14222` P1-3 — the chokepoint refuses to defer a write the record was
        # never going to take). The DSN is never connected to; it is only what
        # `record_dsn()` validates.
        monkeypatch.setenv(projection_worker.ENV_WORKER, "1")
        monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
        projection_worker.shutdown(drain=False)
        try:
            seed = tool_mod._seed_population(
                _FoldDouble(),
                [{"id": "pt-12186000000", "content": "alpha"}],
                journal_path=tmp_path / "j.jsonl",
                # ⛔ THE TENANT IS CARRIED BY VALUE, and that is deferral gate 4:
                # a `threading.Thread` does not inherit the caller's contextvars,
                # so a write with no carried tenant would be resolved again on the
                # worker (#9192's mis-scoping class) and `live_write` folds it
                # inline instead. Without this pair the leg measures the inline arm,
                # not the deferred one.
                tenant=("org-1", "graph-1"),
                encode_many=lambda texts: [[0.5] for _ in texts],
            )
        finally:
            projection_worker.shutdown(drain=True, timeout=5)

        payload = seed.as_dict()
        assert seed.record_deferred == 1, payload
        assert (seed.record_applied, seed.record_unprojected) == (0, 0), payload
        assert seed._fold_needed() is False, payload
        # ⛔ THE PREDICATE IS READ HERE, ON THE REPORT THE DEFERRED RUN ACTUALLY
        # PRODUCED (#14222 review, P3). It used to be asserted on a hand-built
        # `SeedReport` with `record_unprojected=0` / `positions_unproven=0`, where
        # `_fold_needed()` answers False by its own INPUTS whatever the deferred
        # path does — the assertion varied under no mutation of the branch this
        # class exists to prove. From this run it DOES vary: make `_fold_needed()`
        # count a deferred write as a fold and this reds, which is the "repair
        # command that cannot work" defect (the queue was already drained).
        assert payload["record_deferred"] == 1, payload
        assert "fold" not in payload, payload.get("fold")
        assert log.positions == [1], (
            "the deferral gate requires a JOURNAL LINE (`not journal_skipped`), so "
            "this leg only means anything if the append actually happened — it "
            f"did not: {log.positions}")

    def test_an_UNDRAINED_deferral_DOES_name_a_repair(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """#14222 review, finding 6 — a drain that did NOT finish is a fold OWED.

        `record_deferred` counts writes whose fold was QUEUED, so on its own it
        says nothing about whether the queue reached them: the seed drains before
        reporting, and a run whose drain returned ``False`` reported
        ``record_deferred: 1, record_unprojected: 0`` and then answered "no fold
        needed" — a report that says the record is whole about positions the loop
        had provably not taken. The predicate must read the REMAINDER, so the
        leg makes a drain fail over a queue that still holds its item and asserts
        the repair is named.

        ⛔ ONLY THE DRAIN'S VERDICT IS SIMULATED. The item is real (the REAL
        chokepoint queues it), the record fold really does not complete (a blocking
        `write_live_record` keeps it in flight), and `record_deferred_undrained`
        comes from the worker's own `stats().pending_positions` — so the reading
        this leg asserts is the one an operator would get, not a patched number.
        Drop the `record_deferred_undrained` term from `_fold_needed()`, or stop
        publishing the count, and this reds.
        """
        from tortoise import projection_worker, record_writer

        applied = types.SimpleNamespace(applied=True, refusal=None, outcome="applied")
        _journal_double(monkeypatch)

        release = threading.Event()

        def _slow_write(record, **_kw):
            # ⛔ THE WORKER'S OWN FOLD, not the caller's: a deferred write never
            # reaches this on the caller's thread, so blocking HERE keeps the queued
            # position IN FLIGHT while the report is built — the worker's
            # `pending_positions` is then a REAL reading of a record the loop has not
            # reached, not a patched number.
            release.wait(timeout=30)
            return applied

        monkeypatch.setattr(record_writer, "write_live_record", _slow_write)
        monkeypatch.setenv(projection_worker.ENV_WORKER, "1")
        monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")

        monkeypatch.setattr(projection_worker, "wait_for_drain", lambda timeout=None: False)
        projection_worker.shutdown(drain=False)
        try:
            seed = tool_mod._seed_population(
                _FoldDouble(),
                [{"id": "pt-12186000001", "content": "beta"}],
                journal_path=tmp_path / "j.jsonl",
                # Carried tenancy is deferral gate 4 — see the sibling leg above.
                tenant=("org-1", "graph-1"),
                encode_many=lambda texts: [[0.5] for _ in texts],
            )
        finally:
            release.set()
            projection_worker.shutdown(drain=True, timeout=10)

        payload = seed.as_dict()
        assert seed.record_deferred == 1, payload
        assert seed.record_deferred_undrained >= 1, (
            "the drain returned False with a position still queued, so the report"
            f" must publish the remainder it left behind: {payload}"
        )
        assert seed._fold_needed() is True, payload
        assert "fold" in payload, payload
        assert payload["record_deferred_undrained"] >= 1, payload
        # ⛔ THE NOTE MUST NAME THE CAUSE (reviewer, P2). The repair's own sentence
        # used to print the OTHER two triggers' zero counts and no reason at all on
        # this arm, so a reader was handed a fold and told nothing about why.
        assert "undrained" in payload["note"], payload["note"]
        assert "0 write(s) came back unprojected and 0 append(s)" not in payload["note"], (
            f"the note still opens with the other arms' zeroes: {payload['note']}"
        )

    def test_a_DRAINED_deferral_whose_FOLDS_ALL_FAILED_still_names_a_repair(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """#14222 review round 16, P2 — a CONVERGED drain is not a WRITTEN record.

        The sibling legs cover "the queue did not reach it" (`record_deferred_
        undrained`) and "a healthy deferred seed" (`_fold_needed()` False). The
        shape between them was invisible: the loop RAN every fold and each one
        FAILED — `circuit_open`, the outage this branch's own vocabulary names.
        ⛔ THIS LEG INDUCES A **RAISING** FOLD: the seam raises, so the loop's own
        `except` mints ``projection_raised`` — NOT `_classify_fold_outcome`, which
        only runs on the RETURN path and never sees a raise (its own docstring
        says the raise arm's ``projection_raised`` "is minted by the caller") — and
        NOT ``projection_refused``. The REFUSAL arm (a store that answers "no row"
        rather than raising) is pinned by its own leg,
        `test_an_ARMED_REFUSAL_is_a_failure_not_a_projection`, not here — say so
        rather than implying this leg covers it (round 17). The queue then
        drains immediately, so `undrained` is 0, `applied` is 0 and `deferred` is N
        — byte-identical to a healthy seed — and the report answered "no fold
        needed" for a record short by every queued row, while
        `require_population_in_record` told the operator to read a `seed.fold` that
        did not exist.

        ⛔ THE COUNTER IS THE WORKER'S OWN, NOT A PATCHED NUMBER: the queued item is
        real (the REAL chokepoint defers it — carried tenancy, journal line, armed
        DSN) and the fold really raises inside the loop, so `stats().failures` moves
        the way it moves in production.

        ⛔ WHAT THIS LEG PINS, AND WHAT IT DOES **NOT** (round 17): deleting the
        `record_deferred_failures` term from `_fold_needed()` reds the
        `_fold_needed() is True` assertion, deleting it from `_fold_note()` reds the
        note assertion, and dropping the key from `as_dict()` reds the payload
        assertion below. ⛔ The DIFFERENCING (`failures - failures_before`) is NOT
        discriminated here — a cumulative read passes this leg too, because a fresh
        worker holds no prior loss to mis-attribute. That is a stated residual, not
        a pin this test does not have.
        """
        from tortoise import projection_worker, record_writer

        def _exploding_write(record, **_kw):
            raise RuntimeError("record unavailable (circuit_open)")

        _journal_double(monkeypatch)
        monkeypatch.setattr(record_writer, "write_live_record", _exploding_write)
        monkeypatch.setenv(projection_worker.ENV_WORKER, "1")
        monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
        projection_worker.shutdown(drain=False)
        try:
            seed = tool_mod._seed_population(
                _FoldDouble(),
                [{"id": "pt-12186000002", "content": "gamma"}],
                journal_path=tmp_path / "j.jsonl",
                tenant=("org-1", "graph-1"),
                encode_many=lambda texts: [[0.5] for _ in texts],
            )
        finally:
            projection_worker.shutdown(drain=True, timeout=10)

        payload = seed.as_dict()
        assert seed.record_deferred == 1, payload
        assert seed.record_deferred_undrained == 0, (
            "the drain converges on a FAILED fold — that is the whole point of this "
            f"leg: {payload}"
        )
        assert seed.record_deferred_failures >= 1, (
            "the loop reported a failed fold and the report did not read it: "
            f"{payload}"
        )
        # ⛔ AND THE ARTIFACT PUBLISHES IT (round 17): the attribute above can be
        # right while `as_dict()` drops the key, and the operator reads the dict.
        assert payload["record_deferred_failures"] >= 1, payload
        assert seed._fold_needed() is True, payload
        assert "fold" in payload, payload
        assert "did not write a row" in payload["note"], payload["note"]

    def test_a_DEFERRED_report_names_NO_repair_and_no_loss(self, tool_mod):
        """The report side of the same fact, pinned without a store.

        `_fold_needed()` must be False for a deferred-only seed: there is nothing
        to fold, because the queue was drained before the report was built. A
        repair emitted here sends the operator after a fold that has already run —
        the same "repair command that cannot work" class as a rejected-by-argparse
        command or a decline with no durable line.

        ⛔ THE PREDICATE IS ASSERTED WHERE IT IS REACHABLE, NOT HERE (#14222
        review, P3). A `SeedReport` built by hand with `record_unprojected=0` and
        `positions_unproven=0` answers `_fold_needed() is False` by its own inputs,
        so asserting it in this leg varied under no mutation — the finding was
        correctly about reachability, not about the value. The varying assertion
        lives in `TestTheDeferredWrite.test_a_DEFERRED_write_is_COUNTED_apart_and_
        names_NO_repair`, on the report the REAL chokepoint produced. What varies
        HERE is the payload: `record_deferred` must survive `as_dict()`.
        """
        report = tool_mod.SeedReport(
            journal="/tmp/j.jsonl",
            org_id="org-1",
            graph_id="graph-1",
            written=2,
            record_armed=True,
            record_applied=0,
            record_unprojected=0,
            record_deferred=2,
            positions_unproven=0,
            folded=2,
            first_seq=1,
            last_seq=2,
        )
        payload = report.as_dict()
        assert payload["record_deferred"] == 2, payload
        assert "fold" not in payload, payload.get("fold")
        assert payload["record_unprojected"] == 0 and payload["record_applied"] == 0, payload


class TestTheArmBlockage:
    """⛔ THE WRITE SEAM MUST AGREE WITH THE RUN ABOUT ARMING (#13839 finding 1).

    `_arm_blockage` is the standing guard behind the declaration fix. A run that
    holds a DSN and whose seam answers *unarmed* wrote NOTHING to the record — so
    the `record_dense` arm has to be blocked AT THAT CAUSE, not by
    `record_population_absent`, whose message would send the operator to a fold
    that cannot repair a store nobody wrote to.
    """

    DSN = "postgresql://user@record.invalid:5432/tortoise"

    def _seed(self, tool_mod, **over):
        base = dict(
            journal="/tmp/j.jsonl",
            org_id="org-1",
            graph_id="graph-1",
            written=3,
            record_armed=True,
            record_applied=3,
            record_unprojected=0,
            positions_unproven=0,
            folded=3,
            first_seq=1,
            last_seq=3,
        )
        base.update(over)
        return tool_mod.SeedReport(**base)

    def test_a_run_holding_a_DSN_whose_seam_says_UNARMED_is_blocked_at_the_cause(
        self, tool_mod
    ):
        blocked = tool_mod._arm_blockage(
            self.DSN,
            self._seed(tool_mod, record_armed=False, record_applied=0, folded=0),
        )
        assert blocked is not None, (
            "a run holding a DSN whose write seam answered 'unarmed' is the mismatch "
            "itself — it must not be reported as a fold shortfall"
        )
        assert blocked.code == "record_arm_undeclared", blocked.code
        assert "TORTOISE_RECORD_DSN" in blocked.message, blocked.message
        assert "not the fold" in blocked.message.lower(), (
            "the refusal must say the fold cannot repair it — naming a reachable "
            "cause that does not apply is the failure #13745 round 6 closed"
        )

    def test_a_run_with_NO_DSN_is_legitimately_unarmed(self, tool_mod):
        """An unarmed deployment is a supported state, not this fault."""
        assert (
            tool_mod._arm_blockage(
                None, self._seed(tool_mod, record_armed=False, record_applied=0, folded=0)
            )
            is None
        )

    def test_an_UNMEASURED_arming_is_not_collapsed_into_this_fault(self, tool_mod):
        """`armed is None` is a different fact: the projections RAISED.

        That arm carries `record_unprojected` and its own fold repair, so a guard
        that read `not armed` would replace a real cause with a wrong one.
        """
        assert (
            tool_mod._arm_blockage(
                self.DSN,
                self._seed(
                    tool_mod,
                    record_armed=None,
                    record_applied=0,
                    record_unprojected=3,
                    folded=0,
                ),
            )
            is None
        )

    def test_an_armed_run_is_not_blocked(self, tool_mod):
        assert tool_mod._arm_blockage(self.DSN, self._seed(tool_mod)) is None

    def test_run_measurement_consults_it(self, tool_mod):
        """⛔ The guard is inert unless the run asks it.

        Measured class of revert: deleting the call site leaves every unit above
        green and restores the silent no-op this PR exists to remove.
        """
        tree = ast.parse(TOOL.read_text())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_arm_blockage"
        ]
        assert calls, (
            "run_measurement no longer consults `_arm_blockage` — a write seam that "
            "answers 'unarmed' on a run holding a DSN would read as a fold shortfall "
            "again (#13839)"
        )
        assigned = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "record_blocked" for t in n.targets)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "_arm_blockage"
        ]
        assert assigned, (
            "the guard's answer must land in `record_blocked`, the arm's named "
            "blockage — a discarded return silently disables it"
        )



# ── 5c. the call sites the review rounds fixed (wiring, not logic) ─────────


class TestTheWiringTheReviewFixed:
    """⛔ A PURITY PIN DOES NOT PIN THE CALL SITE.

    Measured: three fixes from rounds 2-3 could each be reverted with the whole
    suite green — `preregistered_n = len(population)` (reinstate the
    own-n bar), deleting `reset_coverage_cache()`, and `_exit_code` returning 0
    (publish a partial artifact as the paired answer). All three are asserted
    here against the shipped source or the shipped helper, because a later lane
    must not be free to reinstate them silently.
    """

    def _run_measurement(self) -> ast.AST:
        tree = ast.parse(TOOL.read_text())
        fn = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        ]
        assert fn, "run_measurement is gone"
        return fn[0]

    def test_the_pre_registered_n_comes_from_the_flag_not_the_built_population(self):
        run = self._run_measurement()
        assigns = [
            n
            for n in ast.walk(run)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "preregistered_n" for t in n.targets)
        ]
        assert assigns, "preregistered_n is never assigned"
        src = ast.dump(assigns[0].value)
        assert "'population'" in src and "Attribute" in src, src
        assert "Call" not in src or "len" not in src, (
            "preregistered_n is computed rather than taken from --population — that "
            "reinstate the run-own-n bar the pre-registration exists to replace"
        )

    def test_the_coverage_cache_is_evicted_after_coverage_and_before_the_arms(self):
        run = self._run_measurement()
        cov = [
            n.lineno
            for n in ast.walk(run)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "require_coverage"
        ]
        reset = [
            n.lineno
            for n in ast.walk(run)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "reset_coverage_cache"
        ]
        arms = [
            n.lineno
            for n in ast.walk(run)
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "ARMS"
        ]
        assert cov and reset and arms
        assert min(cov) < min(reset), "the population-scoped verdict is never evicted"
        assert min(reset) < min(arms), (
            "the eviction lands AFTER the arms — the shipped read path would then "
            "consume this population's denominator"
        )
        # ⛔ AND THE READINESS CACHE, FOR THE SAME REASON ONE STORE OVER (#13830).
        # `journal_ready` memoises per (tenant, journal) for 30 s; a NOT-READY
        # verdict taken BEFORE the seed would be reused by the arms and would send
        # `record_dense` to the graph — this run refusing on its own earlier state.
        ready = [
            n.lineno
            for n in ast.walk(run)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "reset_readiness_cache"
        ]
        assert ready, "the readiness verdict taken before the seed is never evicted"
        assert max(cov) < min(ready) < min(arms), (
            "the readiness cache must be evicted AFTER the seed (which is what makes "
            "the tenant ready) and BEFORE the first arm"
        )

    def test_the_incomplete_run_exits_non_zero(self, tool_mod):
        """The artifact is written, so the exit code is the ONLY incompleteness signal."""
        assert tool_mod._exit_code(None) == 0
        assert tool_mod._exit_code(tool_mod.Refused("record_population_absent", "x")) == 3

    @pytest.mark.parametrize("flag", ["--population", "--points", "--min-population"])
    def test_a_non_positive_count_is_an_argument_error_not_a_crash(self, tool_mod, capsys, flag):
        """⛔ Exit `1` WITH A TRACEBACK is not in the exit table, and it was reachable.

        `--population/--points/--min-population` are argparse-`type=int`, so a
        non-INTEGER is rejected by argparse (exit 2, usage, no marker) — but `0`
        and `-1` pass argparse and reached `detectable_effect_pp`, which raises a
        bare `ValueError` that `main()` never caught. A caller got exit **1** and
        a traceback: the one code it cannot tell from a crash, on the path
        `--preregister`'s own help advertises as touching no store. Measured
        before the fix: `--preregister --population 0` -> exit 1, ValueError.

        These are validated by the tool itself, so they are exit **4** with a
        `REFUSED:` marker — and the marker is asserted, because the whole point is
        that the caller can tell these apart from argparse's marker-less exit 2.
        """
        for bad in ("0", "-1"):
            capsys.readouterr()
            rc = tool_mod.main(["--preregister", flag, bad])
            err = capsys.readouterr().err
            assert rc == 4, f"{flag} {bad} gave {rc}, not the documented argument code"
            assert f"REFUSED: {flag} must be >= 1" in err, err

    def test_argparse_typed_flags_exit_2_without_a_marker_and_ks_does_not(self, tool_mod, capsys):
        """⛔ THE EXIT TABLE MUST NOT PREDICT A CODE THE TOOL DOES NOT PRODUCE.

        It did: the docstring listed `--ks non-str` among the argparse-TYPED flags
        that fail through `parser.error()` into exit 2. `--ks` has no `type=`, so
        argparse never validates it — `--ks abc` exits **4** with
        `REFUSED: --ks must be comma-separated integers`. A caller branching on
        `2` vs `4` therefore branched wrongly on the flag the docstring named.
        """
        # --deep IS argparse-typed: a non-integer is argparse's, exit 2, no marker.
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            tool_mod.main(["--deep", "notanint"])
        assert exc.value.code == 2
        assert "REFUSED" not in capsys.readouterr().err
        # --ks is NOT: the tool parses it, so it is exit 4 WITH the marker.
        capsys.readouterr()
        assert tool_mod.main(["--ks", "abc", "--deep", "10"]) == 4
        assert "REFUSED: --ks must be comma-separated integers" in capsys.readouterr().err

    def test_the_exit_code_decision_is_NOT_nested_inside_the_out_branch(self):
        """⛔ THE CALL SITE, not the helper — this is where the false PASS lived.

        Measured: `_exit_code` was pinned (`0`/`3`) while the call site sat INSIDE
        the `else:` of `if out:`, so the DOCUMENTED invocation (`--out …`) wrote
        the artifact, printed only `wrote …`, and returned `0` on a BLOCKED arm.
        The helper was green; the run reported success. A pin on a pure helper
        cannot see a branch, so this asserts the SHAPE of `run_measurement`:
        its final statement returns `_exit_code(...)`, and no bare `return 0`
        survives anywhere in the function.
        """
        tree = ast.parse(TOOL.read_text())
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        last = run.body[-1]
        assert isinstance(last, ast.Return), ast.dump(last)[:120]
        assert isinstance(last.value, ast.Call), ast.dump(last.value)[:120]
        assert isinstance(last.value.func, ast.Name), ast.dump(last.value)[:120]
        assert last.value.func.id == "_exit_code", (
            "run_measurement must END by returning _exit_code(blocked) — a bare "
            "`return 0` (or a _exit_code call nested in a branch) reports success "
            "for a blocked run on the --out path"
        )
        # ⛔ AND IT MUST PASS `blocked`, NOT ITS LOOKALIKE. `run_measurement`
        # holds TWO near-identical locals (`record_blocked` from the DSN/tenant
        # gates, `blocked` from the cohort/id gates). `_exit_code(record_blocked)`
        # is green on every assertion above AND returns 0 on the only block path
        # a real run reaches (`record_population_absent` never touches
        # `record_blocked`) — the P0 this pin exists to close, restored.
        arg = last.value.args[0] if last.value.args else None
        assert isinstance(arg, ast.Name), ast.dump(arg)[:120]
        assert arg.id == "blocked", (
            f"the final exit must be `_exit_code(blocked)`, not "
            f"`_exit_code({getattr(arg, 'id', '?')})` — `record_blocked` is None on "
            "the cohort/id block path, so that spelling reports success for an "
            "incomplete run"
        )
        # ⛔ ONE RETURN. An early `return _exit_code(record_blocked)` in a branch
        # would leave `body[-1]` correct while adding a second, wrong exit.
        returns = [n for n in ast.walk(run) if isinstance(n, ast.Return)]
        assert len(returns) == 1, (
            "run_measurement must have exactly one `return`, found "
            f"{[(n.lineno, ast.unparse(n)[:40]) for n in returns]}"
        )
        assert not [
            n
            for n in returns
            if isinstance(n.value, ast.Constant) and isinstance(n.value.value, int)
        ], "run_measurement still returns a literal int — every exit must route through _exit_code"

    def test_both_bars_bind_so_a_smaller_population_cannot_loosen_the_gate(self, tool_mod):
        """⛔ A run that built FEWER claims gets a LARGER observed bar.

        `detectable_effect_pp` is `2.8016·sqrt(p_d/n)`, so with the pre-registered
        n fixed, a smaller run has the easier committed bar. Gating on the
        pre-registered bar ALONE therefore published GAIN at deltas the
        population cannot resolve — the false-PASS class, in the direction that
        flatters the arm. Both bars must bind.
        """
        cmp = {
            "delta_pp": 4.80,
            "mcnemar_p_two_sided": 1e-9,
            "detectable_pp": 5.12,  # observed at n = 600
            "detectable_pp_preregistered": 4.47,  # committed at n = 786
            "found_deep_delta_pp": 0.0,
        }
        assert tool_mod.classify(cmp) == "UNRESOLVED"
        cmp["delta_pp"] = 5.50  # above BOTH
        assert tool_mod.classify(cmp) == "GAIN"

    def test_the_artifact_quotes_THE_SAME_BAR_the_verdict_used(self, tool_mod):
        """⛔ ONE definition of the deciding bar, and the artifact must read it.

        `detectable_pp_gated` was hard-wired to `detectable_pp_preregistered`
        while `classify` gated on `max(...)`. In the undersized range
        (`600 <= built_n < 786`, reachable from the documented recipe) the
        OBSERVED bar is the larger one, so a reader quoting the field concluded
        GAIN where the label said UNRESOLVED — the misreport the field exists to
        prevent. It was also unpinned: replacing it with `0.0` left the suite
        green.
        """
        for observed, preregistered, expected in (
            (5.12, 4.47, 5.12),  # undersized run: the OBSERVED bar decides
            (3.23, 4.47, 4.47),  # oversized run: the PRE-REGISTERED bar decides
            (4.47, None, 4.47),
            (None, 4.47, 4.47),
        ):
            cmp = {
                "delta_pp": 4.80,
                "mcnemar_p_two_sided": 1e-9,
                "detectable_pp": observed,
                "detectable_pp_preregistered": preregistered,
                "found_deep_delta_pp": 0.0,
            }
            assert tool_mod.gated_bar_pp(cmp) == expected, (observed, preregistered)
        assert (
            tool_mod.gated_bar_pp({"detectable_pp": None, "detectable_pp_preregistered": None})
            is None
        )
        # …and the artifact field is not a second spelling of it.
        src = TOOL.read_text()
        assert '"detectable_pp_gated": gated_bar_pp(cmp)' in src, (
            "detectable_pp_gated must be computed by gated_bar_pp, the SAME "
            "function classify gates on — a literal here is a second answer"
        )


# ── 5c. THE DOCUMENTED RUN PATH, DRIVEN END TO END ────────────────────────


class _FakeGraph:
    # The texts this run "seeded", set by `_drive`. Class-level because
    # `run_measurement` constructs the SDK (and therefore this graph) itself.
    # ⛔ IT ANSWERS THE ID-AND-TEXT QUERY HONESTLY RATHER THAN THE RUN STUBBING
    # `graph_population_vectors`: stubbing it made the guard's enforcement
    # unobservable — a run that swallowed the refusal kept the whole suite green
    # (`#13745` review round 1, finding 1).
    contents: ClassVar[dict[str, str]] = {}
    # ⛔ AN UNREADABLE GRAPH, driven through `run_measurement`. The unit-level
    # `_GraphStub(boom=…)` test proves the FUNCTION answers None, but nothing
    # drove the RUN with a graph it could not read — so `graph_population or 0`
    # in the artifact published `0` ("the graph holds no vectors") for a graph
    # that could not be read, with 99 tests green (`#13745` review round 11).
    unreadable: ClassVar[bool] = False
    # A graph that is DOWN answers NEITHER shape — the realistic case. `unreadable`
    # alone models only a failing id-scoped read, which is what the round-11 test
    # drove; a dead store also fails the PREFIX count, the coverage denominator.
    down: ClassVar[bool] = False
    # Only the PREFIX scan fails (a label-wide scan can time out on a store that
    # answers id lookups) — the shape the round-12 `graph_unreadable` guard
    # exists for, and the one `unreadable`/`down` do not model
    # (`#13745` review round 14, finding 1: repointing the down-graph test at the
    # WRITE left that guard asserted nowhere, so deleting it stayed green).
    prefix_unreadable: ClassVar[bool] = False
    # ⛔ AND AN UNOPENABLE GRAPH IS ITS OWN CASE (`#13745` review round 16). A
    # store that is DOWN fails the OPEN-TIME health probe inside `_get_proj()`,
    # before `_seed_population` is ever called — so the write guard cannot see it and
    # the run still reached `main()` as a traceback. `unreadable`/`down`/
    # `prefix_unreadable` all require a store that answered the open, so without
    # a knob of its own the guard that actually covers "the store is down" is
    # asserted nowhere. The knob lives on `_FakeSDK`, which owns `_get_proj`.

    def __init__(self, rows: int):
        self._rows = rows

    def query(self, _cypher, params=None):
        if self.down:
            raise RuntimeError("no route to the graph")
        if params is not None and "ids" in params:
            if self.unreadable:
                raise RuntimeError("no route to the graph")
            return types.SimpleNamespace(
                result_set=[[i, self.contents.get(i)] for i in params["ids"]]
            )
        # The run's PREFIX-wide count: `graph_vectors`, the coverage DENOMINATOR.
        if self.prefix_unreadable:
            raise RuntimeError("prefix scan timed out")
        return types.SimpleNamespace(result_set=[[f"pt-12186{i:06d}"] for i in range(self._rows)])


class _FakeProj:
    def __init__(self, rows: int):
        self.g = _FakeGraph(rows)


class _FakeSDK:
    # ⛔ THE PREFIX-WIDE COUNT IS ITS OWN KNOB, DELIBERATELY NOT `len(population)`.
    # Fixed at the population's own 3 it made the artifact's two counts equal by
    # construction, so emitting one count under BOTH names — the collapse the pair
    # exists to prevent — stayed green across the suite (`#13745` review round 2,
    # finding 1: `"graph_vectors": graph_population` survived 95 tests). The
    # population is always 3 rows here; a test that sets this to anything else
    # makes the two artifact fields DISAGREE.
    prefix_rows: ClassVar[int] = 3
    # a graph that fails the OPEN-TIME health probe — declared HERE, on the class
    # that reads it, so a test that builds `_FakeSDK` directly still gets a default
    unopenable: ClassVar[bool] = False

    def __init__(self, **_kw):
        self._proj = _FakeProj(self.prefix_rows)

    def _get_proj(self):
        if self.unopenable:
            # the real shape: `FalkorProjection.from_uri` runs the open-time
            # health probe and raises a plain RuntimeError when it cannot answer
            raise RuntimeError(
                "DB health check failed on open (server/production mode). "
                "Run with an embedded store or fix the connection."
            )
        return self._proj


class TestTheDocumentedRunPath:
    """⛔ THE RUNBOOK'S OWN INVOCATION, EXECUTED — not the helper it relies on.

    `#13734` round 4, finding 1: `--out` (the flag every document prescribes)
    swallowed the incomplete-run exit code, and the pin that existed called the
    pure helper `_exit_code`, which is why the revert stayed green. `afc93edcbf`
    fixed the branch and pinned the SHAPE; the behavioural half is here, because
    the shape pin says where the `return` sits and this one says what a caller
    sees.

    ⛔ THE ARMS ARE FAKED; THE WIRING IS THE SHIPPED ONE. `require_corpus`,
    `require_embedder`, the record preconditions, `_seed_population` and `_read_arm`
    are stubs, so no store, corpus or embedder is needed — everything from the
    population build to the exit code is `run_measurement` as it ships.
    """

    SCOPE = types.SimpleNamespace(org_id="org-1", graph_id="graph-1")

    BLOCK_MESSAGE = (
        "TORTOISE_RECORD_DSN is not set — the record's dense leg cannot be read, "
        "and the graph's leg under the record's name is not this measurement"
    )

    def _drive(
        self,
        tool_mod,
        monkeypatch,
        tmp_path,
        *,
        out: str,
        preregistered_n: int,
        seeded: int | None = None,
        record_ok: bool = False,
        graph_unreadable: bool = False,
        graph_down: bool = False,
        prefix_unreadable: bool = False,
        seed_refused: str | None = None,
        graph_unopenable: bool = False,
        population_in_record: int | None = None,
        seed_unprojected: int = 0,
    ):
        self.coverage_kwargs = {}
        corpus = tmp_path / "corpus"
        corpus.mkdir(exist_ok=True)
        rows = [
            {"id": f"pt-12186{i:06d}", "content": f"claim {i}", "query": f"query {i}"}
            for i in range(3)
        ]
        monkeypatch.setattr(tool_mod, "require_corpus", lambda _d: [corpus / "a.jsonl"])
        monkeypatch.setattr(tool_mod, "require_embedder", lambda: None)
        monkeypatch.setattr(tool_mod, "_resolve_dsn", lambda _explicit: None)
        # ⛔ THE DSN DECLARATION IS THE RUN'S, NOT THIS PROCESS'S (#13839 review,
        # finding 1). `run_measurement` publishes the resolved DSN into
        # `TORTOISE_RECORD_DSN` for the write seam and `main()` restores it — but
        # every test here calls `run_measurement` DIRECTLY, which is the path with no
        # restore. `delenv` records "was unset" and monkeypatch's teardown deletes
        # whatever the run left behind, so a stub run cannot arm the record for the
        # NEXT test in this session (the same hazard the journal declaration has).
        from tortoise.record_writer import RECORD_DSN_ENV

        monkeypatch.delenv(RECORD_DSN_ENV, raising=False)

        def _no_record(_dsn):
            raise tool_mod.Refused("no_record_dsn", self.BLOCK_MESSAGE)

        monkeypatch.setattr(tool_mod, "require_record", _no_record)
        if record_ok:
            # ⛔ A HEALTHY RECORD, so the property this change exists for — that a
            # partial graph seed does NOT cost the headline
            # `record_dense_vs_lexical` — is observable. Without it the run-path
            # harness blocks `record_dense` for an unrelated reason and the claim
            # is asserted nowhere (`#13745` review round 2, finding 2).
            monkeypatch.setattr(tool_mod, "require_record", lambda _dsn: "postgresql://u@h/db")
            # `run_measurement` imports `record_vector` INSIDE the function, so the
            # seam is the module on `tortoise`, not an attribute of the tool.
            monkeypatch.setattr("tortoise.record_vector._resolve_scope", lambda: self.SCOPE)

            # ⛔ AND WHAT IT WAS ASKED WITH IS CAPTURED: `graph_vectors` is the
            # coverage DENOMINATOR, so `graph_vectors=0` silently turns the gate
            # into `record_vectors >= 0` — always verified — while the artifact
            # still publishes `verified: true` (`#13745` review round 12).
            def _coverage(*_a, **_k):
                self.coverage_kwargs.update(_k)
                return types.SimpleNamespace(
                    verified=True, reason="ok", record_vectors=3, graph_vectors=3
                )

            monkeypatch.setattr("tortoise.record_vector.coverage", _coverage)
            monkeypatch.setattr("tortoise.record_vector.reset_coverage_cache", lambda: None)
            monkeypatch.setattr(
                tool_mod,
                "record_population_vectors",
                lambda _ids, _scope, _dsn: (
                    3 if population_in_record is None else population_in_record
                ),
            )
        monkeypatch.setattr(tool_mod, "_git_head", lambda: "deadbeef")
        # ⛔ WHAT THE GRAPH "HOLDS" IS WRITTEN BY THE SEED STUB, NOT PRE-SET HERE.
        # Pre-setting it made the graph independent of whether the seed ran, so a
        # count taken BEFORE `_seed_population` — which on a first run blocks
        # `graph_dense` on every box — stayed green across the whole suite
        # (`#13745` review round 4, finding 1: the `stub the outcome` shape round 1
        # fixed for `graph_population_vectors`, reintroduced one level up).
        # `seeded` is how many of this run's rows the seed lands; the rest keep no
        # vector (or an earlier text's).
        _FakeGraph.contents = {}  # nothing is in the graph until the seed writes it
        # …and whether the id-scoped read can be TAKEN at all. `unreadable` makes
        # `query` raise for that shape, which is the only way to drive the
        # None-vs-0 contract through the RUN instead of the pure function.
        _FakeGraph.unreadable = graph_unreadable
        _FakeGraph.down = graph_down
        _FakeGraph.prefix_unreadable = prefix_unreadable
        _FakeSDK.unopenable = graph_unopenable

        def _seed(_proj, pop, *, journal_path=None, tenant=None, record_dsn=None):
            # ⛔ WHAT THE SEED WAS TOLD IS CAPTURED, NOT IGNORED (#13839 review,
            # finding 1). The run's DSN reaches the seed so the artifact can publish
            # the STORE it wrote; a stub without the keyword would let that wiring
            # be dropped with the suite green, which is the class of gap this review
            # exists for.
            self.seed_dsn = record_dsn
            if seed_refused is not None:
                # ⛔ A `Refused` FROM INSIDE THE SEED MUST SURVIVE THE WRITE
                # GUARD. It is a `RuntimeError`, so the guard's broad
                # `except Exception` would swallow it and re-label a row the
                # EMBEDDER could not embed as `graph_unwritable` — sending the
                # operator to FalkorDB for a refusal that fires BEFORE any write
                # (`#13745` review round 15). `except Refused: raise` looks
                # redundant next to the clause below it, and is not.
                raise tool_mod.Refused(seed_refused, "the embedder answered no vector")
            if graph_down:
                # a store that will not accept the write either: the realistic
                # dead-graph case, and the one the READ guard cannot see
                raise RuntimeError("no route to the graph")
            keep = len(pop) if seeded is None else seeded
            _FakeGraph.contents = {r["id"]: r["content"] for r in pop[:keep]}
            # ⛔ A `SeedReport`, NOT AN INT (#13830): the artifact publishes what
            # the write path answered, so the stub must speak the real return
            # shape or the run-path tests would be exercising a signature the
            # shipped seed does not have.
            return tool_mod.SeedReport(
                journal=str(journal_path),
                org_id="org-1",
                graph_id="graph-1",
                written=len(pop),
                record_armed=True,
                record_applied=len(pop) - seed_unprojected,
                record_unprojected=seed_unprojected,
                positions_unproven=0,
                folded=len(pop),
                first_seq=1,
                last_seq=len(pop),
            )

        monkeypatch.setattr(tool_mod, "_seed_population", _seed)
        # ⛔ THE JOURNAL IS DECLARED, NOT ASSUMED (#13830). `require_journal`
        # refuses when neither `--journal` nor the env names one, and the run
        # DECLARES the resolved path for the record arm's readiness gate — so
        # `monkeypatch` owns the path here (and restores the environment after).
        monkeypatch.setenv("TORTOISE_RECORD_JOURNAL", str(tmp_path / "journal" / "events.jsonl"))
        monkeypatch.setattr(
            tool_mod,
            "_read_arm",
            # The lexical arm misses every claim, the dense arms hit: the pairing
            # then has discordant pairs, so the resolution bars are computed
            # instead of being `None` (an all-agreeing trace has none).
            lambda _sdk, arm, queries, _deep, _sanitise: (
                {qid: (5 if arm == tool_mod.ARM_LEXICAL else 1) for qid, _ in queries},
                [],
                {"arm": arm, "ran": True, "mechanisms": ["index"]},
            ),
        )
        monkeypatch.setattr("tools.window_quality_m2.harvest_turn_texts", lambda _s, _n: ([{}], {}))
        monkeypatch.setattr(
            "tools.window_quality_m2.build_point_population",
            lambda _texts, _points, sanitise=None, stats=None: [dict(r) for r in rows],
        )
        monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)

        args = types.SimpleNamespace(
            sessions_dir=str(corpus),
            dsn="",
            population=preregistered_n,
            points=3,
            min_population=1,
            deep=tool_mod.DEEP_LIMIT_DEFAULT,
            namespace="claimdense_test",
            graph="",
            journal="",
            out=out,
        )
        return tool_mod.run_measurement(args)

    def test_the_documented_out_run_exits_3_and_names_the_refusal(
        self, tool_mod, monkeypatch, tmp_path, capsys
    ):
        """`--out` must not change the exit status nor silence the refusal."""
        target = tmp_path / "claim-dense-13732.json"
        rc = self._drive(tool_mod, monkeypatch, tmp_path, out=str(target), preregistered_n=M2_N)
        err = capsys.readouterr().err
        assert target.exists(), "the artifact must still be written"
        assert rc == 3, f"--out swallowed the incomplete-run exit (rc={rc})"
        assert f"REFUSED[no_record_dsn]: {self.BLOCK_MESSAGE}" in err, err

    def test_the_stdout_run_keeps_the_same_exit_and_the_same_refusal(
        self, tool_mod, monkeypatch, tmp_path, capsys
    ):
        """The two output modes must be the same run with a different sink."""
        rc = self._drive(tool_mod, monkeypatch, tmp_path, out="", preregistered_n=M2_N)
        got = capsys.readouterr()
        assert rc == 3
        assert json.loads(got.out)["arms"]["record_dense"]["blocked"] == "no_record_dsn"
        assert f"REFUSED[no_record_dsn]: {self.BLOCK_MESSAGE}" in got.err, got.err

    def test_the_artifact_carries_BOTH_graph_counts(self, tool_mod, monkeypatch, tmp_path):
        """Both counts, because the prefix-wide one cannot tell a full seed from a partial one.

        ⛔ The README tells the reader the artifact "carries both counts", so the
        claim needs a pin: a mutant that deletes these two fields, or that emits
        the prefix count under both names, left the suite green.
        """
        target = tmp_path / "a.json"
        # ⛔ THE PREFIX COUNT IS DRIVEN TO A VALUE THE POPULATION IS NOT. With both
        # fixed at 3 the fields agree by construction and the collapse mutant
        # (`"graph_vectors": graph_population`) survives every assertion below.
        # 5 vs 3 makes it visible (`#13745` review round 2, finding 1).
        monkeypatch.setattr(_FakeSDK, "prefix_rows", 5)
        # a HEALTHY record, so the coverage gate is actually CONSULTED (it is the
        # consumer of the denominator this test pins)
        self._drive(
            tool_mod, monkeypatch, tmp_path, out=str(target), preregistered_n=M2_N, record_ok=True
        )
        artifact = json.loads(target.read_text())
        pop = artifact["population"]
        assert pop["graph_vectors"] == 5, pop  # the PREFIX-wide count (_FakeGraph)
        assert pop["graph_population_vectors"] == 3, pop  # the id-and-text count (guard)
        # ⛔ and each field must carry ITS OWN measurement, not one count under two
        # names — `"graph_population_vectors": graph_vectors` satisfies every
        # assertion above while telling the reader nothing.
        # ⛔ THE DENOMINATOR ITSELF, not just its presence: `graph_vectors=0`
        # makes `record_vectors >= graph_vectors` always true.
        # ⛔ AND THE HEALTHY CASE MUST STILL READ AS VERIFIED. Without this, the
        # `denominator_zero` branch firing ALWAYS stayed green while every artifact
        # declared the gate unverifiable (`#13745` review round 14, finding 2).
        assert artifact["coverage"]["verified"] is True, artifact["coverage"]
        assert self.coverage_kwargs.get("graph_vectors") == 5, (
            "the coverage gate must be asked with the PREFIX count as its "
            f"denominator, got {self.coverage_kwargs!r}"
        )
        src = TOOL.read_text()
        assert '"graph_population_vectors": graph_population' in src, (
            "the artifact's id-and-text count must be `graph_population`, the value "
            "the guard refused on — not `graph_vectors` under a second name"
        )
        assert '"graph_vectors": graph_vectors' in src, (
            "the artifact's prefix-wide count must be `graph_vectors` — bound to "
            "`graph_population` instead, the coverage denominator is not reported "
            "and both fields say the same thing"
        )

    def test_a_partly_seeded_graph_costs_the_graph_arm_and_NOTHING_ELSE(
        self, tool_mod, monkeypatch, tmp_path, capsys
    ):
        """THE BEHAVIOUR, through the shipped guard, with a HEALTHY record.

        ⛔ `#13745` review round 1, finding 1: stubbing `graph_population_vectors`
        in the run-path harness left the guard's enforcement unobservable — a run
        that swallowed its refusal kept the suite green.
        ⛔ AND ROUND 2, FINDING 2: the harness blocked `record_dense` too (a
        deliberate `no_record_dsn`), so the property this whole change exists for
        — that a partial graph seed does NOT cost the headline
        `record_dense_vs_lexical` — was asserted nowhere, and a mutant that
        suppressed the headline whenever the graph arm was blocked stayed green.
        So `record_ok=True`: only the graph arm may be missing from the result.
        """
        target = tmp_path / "partial.json"
        rc = self._drive(
            tool_mod,
            monkeypatch,
            tmp_path,
            out=str(target),
            preregistered_n=M2_N,
            seeded=1,
            record_ok=True,
        )
        artifact = json.loads(target.read_text())
        # the graph arm is blocked BY NAME and carries no measurement
        assert artifact["arms"]["graph_dense"]["blocked"] == "graph_population_absent"
        assert "summary" not in artifact["arms"]["graph_dense"]
        assert artifact["population"]["graph_population_vectors"] == 1
        # ⛔ THE OTHER TWO ARMS ARE REAL MEASUREMENTS — this is the discriminator
        # against run-level scoping (which raises before the first arm, exits 2
        # and publishes NO artifact) and against a graph block that leaks into the
        # record arm (round 2, finding 1: `graph_unseeded` blocking `record_dense`).
        assert "summary" in artifact["arms"]["lexical"], artifact["arms"]["lexical"]
        assert "summary" in artifact["arms"]["record_dense"], artifact["arms"]["record_dense"]
        # ⛔ AND THE HEADLINE SURVIVES. Only comparisons that involve the blocked
        # arm are UNMEASURED; `record_dense_vs_lexical` is a real verdict.
        assert artifact["comparisons"]["record_dense_vs_lexical"]["label"] != "UNMEASURED"
        assert "primary" in artifact["comparisons"]["record_dense_vs_lexical"]
        assert artifact["comparisons"]["graph_dense_vs_lexical"]["label"] == "UNMEASURED"
        assert artifact["comparisons"]["record_dense_vs_graph_dense"]["label"] == "UNMEASURED"
        # INCOMPLETE, named, exit 3 — and exactly ONE cause, since the record is healthy
        assert rc == 3
        notes = [n for n in artifact["not_measured"] if "BLOCKED[" in n]
        assert len(notes) == 1, notes
        assert "BLOCKED[graph_population_absent]" in notes[0], notes[0]
        assert [line for line in capsys.readouterr().err.splitlines() if "REFUSED[" in line] == [
            "REFUSED[graph_population_absent]: " + artifact["arms"]["graph_dense"]["message"]
        ]

    def test_a_graph_with_NO_vector_at_all_still_measures_the_other_arms(
        self, tool_mod, monkeypatch, tmp_path, capsys
    ):
        """The run-level `graph_vectors <= 0` refusal is GONE — and pinned gone.

        ⛔ `#13745` review round 3, finding 3: every run-path test drove the
        prefix count at 3, so `graph_vectors == 0` was never executed and
        re-inserting `if graph_vectors <= 0: raise Refused("graph_unseeded", …)`
        ahead of the arms stayed green (95 passed) — the deletion this change is
        named for had no behavioural pin. The zero-vector graph is the EXTREME of
        a partly seeded one: the graph arm is blocked BY NAME, the other two
        measure, and the run exits 3.
        """
        monkeypatch.setattr(_FakeSDK, "prefix_rows", 0)
        target = tmp_path / "zero.json"
        rc = self._drive(
            tool_mod,
            monkeypatch,
            tmp_path,
            out=str(target),
            preregistered_n=M2_N,
            seeded=0,
            record_ok=True,
        )
        artifact = json.loads(target.read_text())
        assert artifact["population"]["graph_vectors"] == 0
        assert artifact["arms"]["graph_dense"]["blocked"] == "graph_population_absent"
        # ⛔ THE DISCRIMINATOR: a run-level refusal raises before the first arm, so
        # it exits 2 with NO artifact and nothing published. Both surviving arms
        # are therefore asserted to be REAL measurements, not just present keys.
        assert "summary" in artifact["arms"]["lexical"], artifact["arms"]["lexical"]
        assert "summary" in artifact["arms"]["record_dense"], artifact["arms"]["record_dense"]
        assert rc == 3, rc

    def test_the_seed_writes_a_row_for_EVERY_entry(self, tool_mod, monkeypatch, tmp_path):
        """BEHAVIOURAL — and complete where an AST pin provably cannot be.

        Three rounds tried to pin "the seed leaves no row unwritten" by inspecting
        `_seed_population`'s syntax, and every attempt was BOTH false-blocking (a
        conditional `raise`, a re-raising `try`, a validation pre-pass all reddened)
        AND blind (a `contextlib.suppress`, a ternary write, a truncated iterable
        all passed) — `#13745` reviews rounds 7-9. Count the WRITES instead: every
        syntactic way of skipping a row shows up at the fold call, so one assertion
        covers the shapes and forbids nothing legitimate.

        ⛔ BUT COUNTING WRITES IS ONLY AS COMPLETE AS THE POPULATION IT COUNTS
        (`#13745` review round 10, P1). With three identical-shape rows the coverage
        was DATA-dependent, not syntax-dependent: a skip keyed on a property the
        fixture never varies stays green — measured, `if not row["content"]:
        continue` → the whole suite PASSED. That is not a hypothetical edit: the
        production write path this seed mirrors computes no vector for falsy content
        (`projection/entities.py` — "computes neither for an operator, falsy content,
        an unavailable embedder, …"), so "preserve the existing vector when the text
        is empty" is the plausible defensive change — and its consequence is a
        silently short population behind a green guard. The fixture therefore VARIES
        every property a skip could key on, and the empty-content row is load-bearing
        rather than decorative: this seed writes a vector for every row regardless of
        the text.
        """
        written: list[str] = []

        class _Proj:
            # ⛔ COUNTED AT THE FOLD (#13830): the chokepoint applies the same
            # journal record the graph receives, so the fold is where every
            # syntactic way of skipping a row shows up. A row that never reached
            # the jamais-appended payload cannot reach `apply`.
            def apply(self, event):
                written.append(event["point"]["id"])

        # falsy content · production-shaped id · varying text length — one row each
        population = [
            {"id": "pt-000000", "content": "claim 0"},
            {"id": "pt-12186-7f3a9c2e", "content": ""},
            {"id": "pt-000002", "content": "a production-shaped claim row, longer than the rest"},
        ]
        _journal_double(monkeypatch)
        try:
            seed = tool_mod._seed_population(
                _Proj(),
                population,
                journal_path=tmp_path / "journal" / "events.jsonl",
                encode_many=lambda texts: [[0.1, 0.2] for _ in texts],
            )
        except tool_mod.Refused as exc:
            # ⛔ A REFUSAL IS NOT A CORRECT OUTCOME FOR THIS POPULATION (`#13745`
            # review round 10, P2). Returning here asserted nothing, and it hid the
            # one fault it can actually observe: a seed that writes every row and
            # THEN refuses makes every run unmeasurable — the outcome this test
            # exists to catch, not to excuse. No legitimate refusal is reachable
            # from here either: `_seed_population`'s only refusals are
            # `seed_vectors_missing` (a length mismatch, or a `None` vector), and the
            # stub above answers a vector for every row. Those two genuine paths are
            # pinned by `test_seed_population_refuses_a_row_it_could_not_embed`.
            pytest.fail(f"a well-formed population must be seeded, not refused: {exc}")
        # ⛔ SORTED, because a fold has no order dependency (`#13745` review
        # round 10, P2): comparing to the population's own order banned a legitimate
        # batching/reordering seed (`reversed(list(zip(…)))` → RED) while catching no
        # hole that the sorted form misses.
        assert sorted(written) == sorted(row["id"] for row in population), (
            "the seed must write one row per population entry — a skipped or "
            f"conditional write leaves a hole: {written}"
        )
        # ⛔ NOT `seed.written == len(population)` ALONE (`#13745` review round 10,
        # P2): the report's count is a claim about the loop, so comparing it to the
        # population would be tautological with respect to the writes — a seed that
        # wrote 1 of 3 rows still reports 3. Tying it to the FOLD is what makes it a
        # claim about the rows.
        assert seed.written == len(written) == len(population), (seed.as_dict(), written)

    def test_a_failed_seed_write_PROPAGATES(self, tool_mod, monkeypatch, tmp_path):
        """The other half: a failed fold must end the run, not be swallowed.

        `graph_population_absent` names only the VECTOR-side causes because a row
        this run failed to write cannot survive to the guard — the failure leaves
        `_seed_population` as an exception. Wrapping the write in
        `contextlib.suppress(Exception)` (this module's cleanup idiom) would make it
        a nameable cause again, which is why this is asserted as behaviour rather
        than as "no `Try` node exists" (`#13745` reviews rounds 7-9).

        ⚠️ A fold failure is now reached through `live_write.write`'s own fold leg,
        which deliberately does NOT catch (`live_write`'s own note: a RECORD fault
        reports and continues, a FOLD fault propagates), so the exception still
        reaches `run_measurement`'s guard.
        """

        class _Proj:
            def apply(self, event):
                if event["point"]["id"].endswith("000001"):
                    raise RuntimeError("the fold refused this row")

        _journal_double(monkeypatch)
        population = [{"id": f"pt-{i:06d}", "content": f"claim {i}"} for i in range(3)]
        with pytest.raises(RuntimeError):
            tool_mod._seed_population(
                _Proj(),
                population,
                journal_path=tmp_path / "journal" / "events.jsonl",
                encode_many=lambda texts: [[0.1, 0.2] for _ in texts],
            )

    def test_a_DOWN_graph_is_a_named_refusal_not_a_traceback(self, tool_mod, monkeypatch, tmp_path):
        """⛔ THE SEED WRITE IS A STORE OPERATION TOO (`#13745` review round 13).

        The prefix READ was made a named refusal in round 12; the seed WRITE that
        runs before it was not, and `main()` catches only `Refused` — so a store
        that accepts the open and then refuses the write gave the operator exit 1,
        a traceback and NO artifact. (The round-12 PREFIX-scan pin lives in
        `test_a_failing_PREFIX_SCAN_is_a_named_refusal_not_a_traceback`; this test
        used to carry that name, which sent a reader looking for `graph_unreadable`
        to a test that never exercises it.)
        """
        with pytest.raises(tool_mod.Refused) as ei:
            self._drive(
                tool_mod,
                monkeypatch,
                tmp_path,
                out=str(tmp_path / "down.json"),
                preregistered_n=M2_N,
                record_ok=True,
                graph_down=True,
            )
        assert ei.value.code == "graph_unwritable"
        assert not (tmp_path / "down.json").exists(), "a refused run publishes nothing"

    def test_a_graph_that_cannot_be_OPENED_is_a_named_refusal(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ THE OPEN IS WHAT A DEAD STORE ACTUALLY FAILS ON (`#13745` round 16).

        `_get_proj()` runs the OPEN-TIME health probe
        (`FalkorProjection.from_uri(..., skip_health_check=False)`), so a graph
        that is simply down raises there — BEFORE `_seed_population`, and therefore
        outside the round-13 write guard, whose comment claimed to cover exactly
        this case while the probe eight lines above it stayed unguarded. `main()`
        catches only `Refused`, so the realistic dead-store run still produced
        exit 1, a traceback and no artifact. Measured: without the open guard this
        test fails with the raw `RuntimeError`, not a `Refused`.
        """
        with pytest.raises(tool_mod.Refused) as ei:
            self._drive(
                tool_mod,
                monkeypatch,
                tmp_path,
                out=str(tmp_path / "unopenable.json"),
                preregistered_n=M2_N,
                record_ok=True,
                graph_unopenable=True,
            )
        assert ei.value.code == "graph_unopenable", ei.value.code
        # ⛔ 2, not 3: 3 means "published, but not the complete paired answer" and
        # this path publishes NOTHING (the arm codes are pinned elsewhere).
        assert ei.value.exit_code == 2, ei.value.exit_code
        assert not (tmp_path / "unopenable.json").exists(), "a refused run publishes nothing"

    def test_a_ZERO_denominator_does_not_publish_a_verified_gate(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ `record_vectors >= 0` is a TAUTOLOGY, not a verification.

        Dropping the run-level `graph_vectors <= 0` refusal made a zero
        denominator reachable, and `record_vector.coverage` then answers VERIFIED
        for any record at all — so the artifact would publish a verified gate off
        the exact state this file calls "a gate that can be satisfied by two
        zeros" (`#13745` review round 13). The RECORD arm is blocked by name; the
        graph arm has its own block; `lexical` still measures.
        """
        monkeypatch.setattr(_FakeSDK, "prefix_rows", 0)
        target = tmp_path / "zero.json"
        rc = self._drive(
            tool_mod,
            monkeypatch,
            tmp_path,
            out=str(target),
            preregistered_n=M2_N,
            record_ok=True,
            seeded=0,  # the graph holds none of this run's vectors either
        )
        artifact = json.loads(target.read_text())
        # the gate is recorded as UNVERIFIABLE, never as a pass
        assert artifact["coverage"]["verified"] is None, artifact["coverage"]
        assert artifact["coverage"]["reason"] == "denominator_zero"
        assert "tautology" in artifact["coverage"]["note"]
        # …and the arms still do what they can: the graph arm is blocked (its own
        # cohort is empty), the RECORD arm measures off its id-scoped guard, and
        # the headline comparison survives
        assert artifact["arms"]["graph_dense"]["blocked"] == "graph_population_absent"
        assert "summary" in artifact["arms"]["record_dense"]
        assert "summary" in artifact["arms"]["lexical"]
        assert artifact["comparisons"]["record_dense_vs_lexical"]["label"] != "UNMEASURED"
        assert rc == 3

    def test_a_Refused_from_the_SEED_keeps_its_own_name(self, tool_mod, monkeypatch, tmp_path):
        """⛔ THE PASSTHROUGH IS LOAD-BEARING: `Refused` ⊂ `RuntimeError`.

        Delete `except Refused: raise` and the broad handler below it swallows a
        seed refusal and re-labels `seed_vectors_missing` as `graph_unwritable` —
        under prose about a store that "will not hold the cohort", when both
        `seed_vectors_missing` refusals fire BEFORE any write at all, so the store
        was never asked (`#13745` review round 15).
        """
        with pytest.raises(tool_mod.Refused) as ei:
            self._drive(
                tool_mod,
                monkeypatch,
                tmp_path,
                out=str(tmp_path / "seed.json"),
                preregistered_n=M2_N,
                record_ok=True,
                seed_refused="seed_vectors_missing",
            )
        assert ei.value.code == "seed_vectors_missing", ei.value.code

    def test_a_failing_PREFIX_SCAN_is_a_named_refusal_not_a_traceback(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ THE ROUND-12 GUARD, ASSERTED WHERE IT LIVES.

        A store that answers id lookups but fails the LABEL-WIDE scan (a plausible
        timeout — the seed's writes are point-level, that query is a scan) used to
        give exit 1 + a traceback + no artifact. Repointing the down-graph test at
        the seed WRITE left this handler asserted NOWHERE, so narrowing or
        deleting it stayed green (`#13745` review round 14, finding 1).
        """
        with pytest.raises(tool_mod.Refused) as ei:
            self._drive(
                tool_mod,
                monkeypatch,
                tmp_path,
                out=str(tmp_path / "scan.json"),
                preregistered_n=M2_N,
                record_ok=True,
                prefix_unreadable=True,
            )
        assert ei.value.code == "graph_unreadable"
        assert not (tmp_path / "scan.json").exists(), "a refused run publishes nothing"

    def test_two_blocked_arms_name_TWO_causes(self, tool_mod, monkeypatch, tmp_path, capsys):
        """⛔ The single-`blocked` slot made the LAST cause win; this pins both.

        A graph block and a record block are different facts about different
        stores, and a report that names only one of them points the next lane at
        the wrong repair. The mutant — reporting `blockages` as a one-element
        slice — is green on every other test in this file.
        """
        target = tmp_path / "two.json"
        rc = self._drive(
            tool_mod, monkeypatch, tmp_path, out=str(target), preregistered_n=M2_N, seeded=1
        )
        artifact = json.loads(target.read_text())
        notes = [n for n in artifact["not_measured"] if "BLOCKED[" in n]
        codes = sorted(n.split("BLOCKED[")[1].split("]")[0] for n in notes)
        assert codes == ["graph_population_absent", "no_record_dsn"], codes
        lines = [line for line in capsys.readouterr().err.splitlines() if "REFUSED[" in line]
        assert len(lines) == 2, lines
        assert {line.split("REFUSED[")[1].split("]")[0] for line in lines} == set(codes)
        # ⛔ EACH COMPARISON MUST NAME ITS OWN BLOCKED ARM'S CAUSE. Pinning only the
        # SET of codes let `next(iter(blockages.values()))` — every comparison
        # reporting the first cause — stay green, so both graph comparisons could
        # carry the RECORD's cause (`#13745` review round 3, finding 2).
        assert artifact["comparisons"]["record_dense_vs_lexical"]["blocked"] == "no_record_dsn"
        assert (
            artifact["comparisons"]["graph_dense_vs_lexical"]["blocked"]
            == "graph_population_absent"
        )
        assert (
            artifact["comparisons"]["record_dense_vs_graph_dense"]["blocked"]
            == "graph_population_absent"
        )
        # ⛔ AND EACH NOTE MUST NAME THE ARM IT BELONGS TO. Pinning only the codes
        # let the note hardcode `record_dense` while carrying a graph cause — the
        # exact "message is the diagnosis" defect this file pins for the record
        # guard (`#13745` review round 3, finding 3).
        by_code = {n.split("BLOCKED[")[1].split("]")[0]: n for n in notes}
        assert by_code["graph_population_absent"].startswith("the `graph_dense` arm"), by_code
        assert by_code["no_record_dsn"].startswith("the `record_dense` arm"), by_code
        assert rc == 3

    def test_an_UNREADABLE_graph_publishes_None_not_zero(
        self, tool_mod, monkeypatch, tmp_path, capsys
    ):
        """⛔ "UNMEASURED IS NEVER ZERO", ASSERTED THROUGH THE ARTIFACT.

        A graph that cannot be read must publish `None` and block the arm by name.
        Publishing `0` says "this graph holds no vectors" — a measurement — when
        the truth is "we could not look", which is the false-PASS class the
        function's own `None`-vs-`0` contract exists to prevent. Nothing drove the
        RUN with an unreadable graph, so `graph_population or 0` survived the whole
        suite (`#13745` review round 11).
        """
        target = tmp_path / "unreadable.json"
        rc = self._drive(
            tool_mod,
            monkeypatch,
            tmp_path,
            out=str(target),
            preregistered_n=M2_N,
            record_ok=True,
            graph_unreadable=True,
        )
        artifact = json.loads(target.read_text())
        assert artifact["population"]["graph_population_vectors"] is None, (
            "an unreadable graph must publish None — `or 0` states a measurement "
            "('no vectors') about a graph nobody could read"
        )
        assert artifact["arms"]["graph_dense"]["blocked"] == "graph_population_unmeasurable"
        assert "summary" not in artifact["arms"]["graph_dense"]
        # the arms that do not read graph vectors still measure, and the run is INCOMPLETE
        assert "summary" in artifact["arms"]["lexical"]
        assert "summary" in artifact["arms"]["record_dense"]
        assert rc == 3
        assert any("BLOCKED[graph_population_unmeasurable]" in n for n in artifact["not_measured"])

    def test_the_artifact_names_the_arms_OWN_reason_not_a_cohort_story(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ Finding 3, asserted through the artifact rather than the branch.

        `not_measured` used to describe every code as a cohort problem, so on the
        common box (no `TORTOISE_RECORD_DSN`) it asserted *"the record does not
        hold this population's cohort"* while `arms["record_dense"]["message"]`
        in the SAME artifact carried the true `no_record_dsn` text: two
        contradictory explanations, the false one pointing the next lane at the
        `#13210` backfill instead of at their env.
        """
        target = tmp_path / "a.json"
        self._drive(tool_mod, monkeypatch, tmp_path, out=str(target), preregistered_n=M2_N)
        artifact = json.loads(target.read_text())
        blocked_notes = [n for n in artifact["not_measured"] if "BLOCKED[" in n]
        assert blocked_notes, artifact["not_measured"]
        assert (
            blocked_notes[0]
            == f"the `record_dense` arm — BLOCKED[no_record_dsn]: {self.BLOCK_MESSAGE}"
        ), blocked_notes[0]
        assert artifact["arms"]["record_dense"]["message"] == self.BLOCK_MESSAGE

    def test_an_UNPROJECTED_seed_is_reported_and_the_arm_still_refuses(
        self, tool_mod, monkeypatch, tmp_path, capsys
    ):
        """⛔ THE TWO HALVES OF #13830, IN ONE RUN.

        The seed reports what the write path answered (`record_unprojected` and the
        exact fold command) — and the id-scoped guard STILL blocks `record_dense`
        when the record did not take the population. Neither half may be traded for
        the other: a run that stayed silent would leave the operator with a blocked
        arm and no repair, and a run that relaxed the guard would publish a figure
        over ids the record does not hold — the `#5090` Q2 false PASS.
        """
        target = tmp_path / "unprojected.json"
        rc = self._drive(
            tool_mod,
            monkeypatch,
            tmp_path,
            out=str(target),
            preregistered_n=M2_N,
            record_ok=True,
            population_in_record=1,
            seed_unprojected=2,
        )
        artifact = json.loads(target.read_text())
        seed_block = artifact["seed"]
        assert seed_block["record_unprojected"] == 2, seed_block
        assert seed_block["fold"]["apply"].startswith(
            "uv run python tools/record_runner.py apply --journal"
        ), seed_block
        assert "--acknowledge-plan" in seed_block["fold"]["apply"], seed_block
        assert "--org-id" in seed_block["fold"]["plan"], seed_block
        # the repair is on stderr too — an artifact read later is not a report
        err = capsys.readouterr().err
        assert "UNPROJECTED" in err and "seed.fold" in err, err
        # ⛔ AND THE ARM STILL REFUSES, BY NAME.
        assert artifact["arms"]["record_dense"]["blocked"] == "record_population_absent"
        assert "record_runner.py plan" in artifact["arms"]["record_dense"]["message"]
        assert rc == 3, rc

    def test_a_clean_seed_publishes_its_counts_and_no_repair(self, tool_mod, monkeypatch, tmp_path):
        target = tmp_path / "clean.json"
        rc = self._drive(
            tool_mod, monkeypatch, tmp_path, out=str(target), preregistered_n=M2_N, record_ok=True
        )
        artifact = json.loads(target.read_text())
        assert artifact["seed"]["record_applied"] == 3, artifact["seed"]
        assert "fold" not in artifact["seed"], artifact["seed"]
        assert artifact["arms"]["record_dense"].get("blocked") is None, artifact["arms"]
        assert rc == 0, rc

    def test_the_run_hands_the_seed_the_dsn_so_the_store_can_be_named(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ THE REPORT'S HALF OF FINDING 1 (#13839).

        The declaration arms the WRITE; this is the other half of the corollary —
        the artifact has to publish WHICH store it wrote, or the emitted repair can
        only name the variable and the operator has no way to know whether their
        shell resolves to the record this run measured. Measured through the shipped
        call site, because a `record_dsn=` keyword that is accepted but never passed
        is exactly the wiring gap the review found.
        """
        target = tmp_path / "dsn.json"
        rc = self._drive(
            tool_mod, monkeypatch, tmp_path, out=str(target), preregistered_n=M2_N, record_ok=True
        )
        assert self.seed_dsn == "postgresql://u@h/db", (
            "the run resolved a DSN and did not pass it to the seed — the artifact "
            "cannot then publish the fingerprint of the store it wrote"
        )
        assert rc == 0, rc

    def test_the_emitted_bar_fields_agree_with_the_emitted_note(
        self, tool_mod, monkeypatch, tmp_path
    ):
        """⛔ Finding 4: the note points a reader at `detectable_pp_gated`.

        When the run is undersized the OBSERVED bar is the larger one, so the
        field claiming to name the deciding bar must carry the observed one. It
        carried the pre-registered one, making the note's own sentence false.
        """
        target = tmp_path / "b.json"
        # 5 pre-registered, 3 built -> the two bars differ, so both fields exist.
        self._drive(tool_mod, monkeypatch, tmp_path, out=str(target), preregistered_n=5)
        artifact = json.loads(target.read_text())
        cmp = artifact["comparisons"]["graph_dense_vs_lexical"]["primary"]
        assert cmp["detectable_pp"] > cmp["detectable_pp_preregistered"], cmp
        assert (
            artifact["comparisons"]["graph_dense_vs_lexical"]["detectable_pp_gated"]
            == (cmp["detectable_pp"])
        ), "the emitted bar is not the one the gate used"
        note = artifact["population_mismatch"]["note"]
        assert "STRICTER" in note, note
        assert "disclosure only" not in note, note


# ── 6. the contradiction test, structurally ───────────────────────────────


def _code_string_literals(tree: ast.AST) -> list[str]:
    """Every string LITERAL the module can hand to an engine — docstrings out.

    ⛔ THIS is the scan, not ``snippet in source``: a source-level substring
    cannot tell a Cypher read from a comment ABOUT that read, so it reds on the
    prose that explains the refusal while never distinguishing it from the
    executable string that would actually reverse the decision.

    Covered by construction: an f-string contributes its literal fragments as
    ``Constant`` nodes, and the parser folds adjacent literals into ONE constant,
    so ``f"CREATE VECTOR INDEX {name}"`` and ``"CREATE VECTOR " "INDEX"`` are
    both seen. Residual, stated rather than implied: two constants joined by
    ``+`` at RUNTIME are not folded, and no literal scan can see that.
    """
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]


def _forbidden_snippets(source: str, snippets: tuple[str, ...]) -> list[str]:
    """Which forbidden snippets appear in ``source``'s CODE string literals."""
    literals = _code_string_literals(ast.parse(source))
    return [snippet for snippet in snippets if any(snippet in lit for lit in literals)]


class TestContradictionTest:
    """Postgres is the record; FalkorDB is the projection (`#11481` V1).

    ⛔ The dense leg must come FROM THE RECORD. The two ways an instrument could
    quietly reverse that are a VECTOR index on the graph and record rows written
    from the harness — both are refused here, by AST, so the refusal is about the
    code and not about a reviewer's memory.

    ⛔ AND ``#13830`` DID NOT REVERSE THE SECOND ONE — it CHANGED ITS ROUTE,
    which is why this docstring is not "record rows are banned" (it said that
    until #13839's review flagged the staleness). The harness now does cause
    record rows, through the ONE sanctioned door: ``live_write.write`` appends the
    journal record and the record projects it on that write's own transaction
    (#13345). What stays banned is every SECOND spelling of that write — a direct
    SQL statement here, or a direct ``write_live_record``/``write_records``/
    ``RecordWriter`` call in the seed (pinned in
    ``TestTheArmsConstruction::test_the_seed_carries_no_direct_write_of_either_store``)
    — because a second path is what can disagree with the journal.
    """

    #: Cypher/DDL this instrument may never contain.
    FORBIDDEN_SNIPPETS: ClassVar[tuple[str, ...]] = (
        "db.idx.vector.queryNodes",  # a graph ANN read would bypass the record
        "CREATE VECTOR INDEX",  # the index that must stay on the record
        "CREATE INDEX FOR",  # ditto, engine-native spelling
        "CREATE INDEX ON",  # ditto, plain-Cypher spelling
    )

    #: ⛔ THE RECORD HALF IS A WRITE-STATEMENT BAN, NOT ONE SPELLING. An earlier
    #: cut denied `INSERT INTO` alone, and a five-item denylist is defeated by an
    #: ordinary synonym on a connection this instrument ALREADY OPENS: `upsert
    #: into public.point …` satisfies `record_population_absent` in three lines
    #: and then publishes a `record_dense` recall of ~100 % that is a figure
    #: about its own seed — the `#5090` Q2 false PASS this guard exists to stop.
    #: So every SQL write shape the tree could plausibly issue is banned, matched
    #: case-insensitively over the CODE literals. Residual, stated rather than
    #: implied: a statement assembled at runtime from non-adjacent pieces (`"ups"
    #: + "ert into"`) is invisible to any literal scan — the same residual the
    #: index half carries, and the reason the runbook says not to relax it.
    RECORD_WRITE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:"
        r"insert\s+into|upsert\s+into|merge\s+into|delete\s+from|"
        r"update\s+\S+\s+set|truncate\s+(?:table\s+)?\S+|alter\s+table|"
        r"create\s+table|copy\s+\S+\s+(?:from|to)"
        r")\b",
        re.IGNORECASE,
    )

    def test_the_instrument_creates_no_graph_vector_index_and_seeds_no_rows(self):
        found = _forbidden_snippets(TOOL.read_text(), self.FORBIDDEN_SNIPPETS)
        assert not found, (
            f"{found} appear in the instrument's CODE — the dense leg comes from "
            f"the RECORD (#11481 V1), and a graph vector index here would "
            f"reverse a landed decision inside a measurement tool"
        )

    def test_the_record_half_bans_every_write_spelling_not_just_INSERT_INTO(self):
        """A synonym must not be enough to seed the record from the harness.

        ⛔ Measured on the pre-fix pin (48 passed with these injected into
        `_seed_population`): `cur.execute("upsert into public.point (id,
        embedding_vec) values (%s, %s)", …)` and `merge into public.point …`.
        """
        literals = _code_string_literals(ast.parse(TOOL.read_text()))
        assert not [lit for lit in literals if self.RECORD_WRITE_RE.search(lit)], (
            "the instrument carries a SQL WRITE statement — the record is fed by a "
            "journal fold (record_vector.py:247), never by this harness"
        )
        # …and the ban is live, not vacuous: every spelling reds.
        for spelling in (
            "INSERT INTO public.point (id) VALUES (%s)",
            "upsert into public.point (id) values (%s)",
            "MERGE INTO public.point USING x ON TRUE",
            "update public.point set embedding_vec = %s",
            "delete from public.point where id = %s",
            "truncate table public.point",
            "alter table public.point add column x int",
            "copy public.point from stdin",
        ):
            assert self.RECORD_WRITE_RE.search(spelling), spelling
        # …and a legitimate read does NOT red (the guard is about writes).
        assert not self.RECORD_WRITE_RE.search(
            "select count(*) from public.point where id = any(%s)"
        )

    def test_the_scan_reads_code_literals_and_not_comments_or_prose(self):
        """The claim is "an AST scan" — pin what that buys, in BOTH directions.

        ⛔ An earlier cut asserted ``snippet not in source`` and the PR body still
        called it an AST scan: a substring over the file reds on a COMMENT or a
        docstring that explains the refusal (the prose is where a reader learns
        which index must stay on the record), which is a false block on correct
        code, and it treats prose and executables as the same event. The scan
        reads the literals the interpreter would hand to an engine.
        """
        snippet = "CREATE VECTOR INDEX"
        prose = (
            '"""Module prose: this instrument never issues CREATE VECTOR INDEX."""\n'
            "# a comment may name CREATE VECTOR INDEX too — that is not a write\n"
            "def f():\n"
            '    """Function prose: CREATE VECTOR INDEX.\n'
            '"""\n'
            "    return 1\n"
        )
        assert _forbidden_snippets(prose, (snippet,)) == []
        assert _forbidden_snippets('def f():\n    return "CREATE VECTOR INDEX "\n', (snippet,)) == [
            snippet
        ]
        assert _forbidden_snippets(
            'def f(name):\n    return f"CREATE VECTOR INDEX {name}"\n', (snippet,)
        ) == [snippet]
        assert _forbidden_snippets(
            'def f():\n    return "CREATE VECTOR " "INDEX"\n', (snippet,)
        ) == [snippet]  # adjacent literals fold into ONE constant

    def test_it_reads_the_record_leg_through_its_own_seam(self):
        """The arm is switched by the module that owns the switch, not re-spelt.

        ⛔ The literal is checked OUTSIDE docstrings: the prose is allowed to
        name the switch (a reader needs it), the CODE is not — one owner of the
        name, so a rename cannot leave this tool flipping a variable nothing
        reads.
        """
        source = TOOL.read_text()
        assert "record_vector.ARMING_ENV" in source
        literals = _code_string_literals(ast.parse(source))
        assert not [lit for lit in literals if "TORTOISE_RECORD_VECTOR_LEG" in lit], (
            "the env var is spelled in CODE as well as in prose — it must be read "
            "through record_vector.ARMING_ENV only"
        )

    def test_it_reuses_the_m3_statistic_rather_than_copying_it(self):
        source = TOOL.read_text()
        assert "from tools.m3_fts_leg_recall import _binom_two_sided" in source


# ── 5b. the GRAPH's membership of the population (id+text scoped, arm-gated) ──


class _GraphResult:
    def __init__(self, result_set):
        self.result_set = result_set


class _GraphStub:
    def __init__(self, result_set=None, boom=None):
        self.result_set = result_set
        self.boom = boom
        self.queries: list[tuple[str, object]] = []

    def query(self, cypher, params=None):
        self.queries.append((cypher, params))
        if self.boom is not None:
            raise self.boom
        return _GraphResult(self.result_set)


class _ProjStub:
    def __init__(self, graph):
        self.g = graph


class TestGraphMembership:
    """The population's OWN ids must carry THIS run's vector in the GRAPH.

    ⛔ The gate beside the seed is PREFIX-wide:
    ``n.id STARTS WITH '<prefix>' AND n.embedding IS NOT NULL``. That count is
    above zero while the population is only PARTLY seeded, and the causes that
    count can actually meet on THIS write path are TWO, not five: the seed
    payload always carries an explicit ``embedding``, so the fold takes its
    ``owns_embedding`` branch and never reaches the ``encode_for_row`` derive —
    an operator point, an EMBED-gate refusal (``#5008``) and a swallowed encode
    failure are UNREACHABLE here (``None`` from ``compute_embeddings`` is refused
    by name as ``seed_vectors_missing`` before any write). What is reachable: a
    width mismatch (``#5004``), and a vector the fold refuses as unusable
    (``_writable_journalled_vector`` → ``None``). A row this run never wrote is
    NOT a cause — ``_seed_population`` upserts every row and raises on failure — it is
    the DEFENSIVE case the text pairing would catch, which is why the pairing is
    kept. These cases red the mutants that make the graph guard prefix-wide,
    id-only, or blind to a row this run did not write — and the wiring pin reds
    moving it off the ``graph_dense`` arm.
    """

    def _pop(self, n: int) -> list[dict]:
        return [{"id": f"pt-12186{i:06d}", "content": f"claim {i}"} for i in range(n)]

    def test_the_query_is_id_scoped_and_not_a_prefix_count(self, tool_mod):
        pop = self._pop(3)
        graph = _GraphStub(result_set=[[r["id"], r["content"]] for r in pop])
        got = tool_mod.graph_population_vectors(_ProjStub(graph), pop)
        assert got == 3
        cypher, params = graph.queries[-1]
        assert "n.id IN $ids" in cypher, cypher  # the ID SET is the predicate
        assert "STARTS WITH" not in cypher, cypher  # ⛔ the prefix-wide mutant
        assert "n.embedding IS NOT NULL" in cypher, cypher  # what the dense leg needs
        assert "RETURN n.id, n.content" in cypher, cypher  # the PAIR, for the pairing below
        assert params == {"ids": [r["id"] for r in pop]}

    def test_a_row_this_run_did_NOT_write_is_not_counted(self, tool_mod):
        """The pairing proves the WRITE REACHED the row — nothing more.

        A node still holding an earlier population's text at this id is a row
        this run did not write (the fold's `n.content=$content` is
        unconditional, so a written row carries THIS run's text). Counting it
        would let `graph_dense` score a population that is partly not there.

        ⛔ AND THE DISTINCTION MATTERS (`#13745` review round 3, finding 1): this
        does NOT make the count a check that the stored VECTOR is this run's. On
        a reused `--graph`, a refused (width-gate) vector leaves this run's text
        beside the OLD vector, and this count passes — the residual is documented
        on `graph_population_vectors`, not asserted away here.
        """
        pop = self._pop(2)
        graph = _GraphStub(
            result_set=[
                [pop[0]["id"], pop[0]["content"]],  # this run's text — counted
                [pop[1]["id"], "an earlier text"],  # stale vector — NOT counted
            ]
        )
        assert tool_mod.graph_population_vectors(_ProjStub(graph), pop) == 1

    def test_an_unreadable_graph_is_unmeasurable_not_empty(self, tool_mod):
        graph = _GraphStub(boom=RuntimeError("no route to the graph"))
        assert tool_mod.graph_population_vectors(_ProjStub(graph), self._pop(3)) is None

    def test_a_malformed_row_is_unmeasurable_not_a_traceback(self, tool_mod):
        """The row shape is the client's; an unexpected one must answer `None`.

        ⛔ Outside the guard, `int(rows[0][0])` (or a 1-column row) raises out of
        `graph_population_vectors` and then out of `run_measurement`, and `main()`
        catches only `Refused` — the operator gets a traceback and exit 1 where
        the artifact promises `REFUSED[...]`. Same shape as the `record_dsn`
        finding. `[[0]]`-style scalar rows are refused as unmeasurable too: this
        query RETURNS a pair, so a bare count is not an answer to it.

        ⛔ AND THE SHORT-ROW CASE IS THE ONE THAT NEEDS THE EXPLICIT CHECK: most
        malformed rows raise on their own (`row[1]` of a 1-element list is an
        `IndexError`), which the `except` would catch anyway — `["not-a-row"]` is
        the case that does NOT, because a long-enough STRING is indexable and
        silently pairs two characters against nothing, returning `0`: an empty
        graph. That is a wrong ANSWER, not a wrong shape, so it must be a refusal
        and this row is the mutant that proves the check earns its line.
        """
        for bad in ([[None]], [["pt-1"]], [[0]], [None], ["not-a-row"], "not-a-row-list"):
            assert (
                tool_mod.graph_population_vectors(_ProjStub(_GraphStub(bad)), self._pop(1)) is None
            ), bad

    def test_an_empty_graph_is_a_measurement_of_zero_not_unmeasurable(self, tool_mod):
        got = tool_mod.graph_population_vectors(_ProjStub(_GraphStub([])), self._pop(3))
        assert got == 0, "an empty result is an empty graph; only a failure is unmeasurable"

    def test_no_population_is_not_a_verdict(self, tool_mod):
        assert tool_mod.graph_population_vectors(_ProjStub(_GraphStub([[0]])), []) is None

    def test_guard_refuses_a_partly_seeded_population(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_graph_population(785, 786)
        assert ei.value.code == "graph_population_absent"
        # the REACHABLE mechanisms are named, not just the arithmetic
        assert "WIDTH" in ei.value.message
        assert "_writable_journalled_vector" in ei.value.message
        # ⛔ THE NUMBERS ARE THE OPERATOR'S DIAGNOSIS — pin them. `{matched}` →
        # `{population_n}` makes the message say "holds an embedding for 786 of the
        # 786" while refusing, and stayed green (`#13745` review round 5, finding 2).
        assert "785 of the 786" in ei.value.message, ei.value.message
        # ⛔ AND THE UNREACHABLE ONES ARE NOT NAMED. The seed payload always carries
        # an explicit `embedding` (so the fold never reaches its `encode_for_row`
        # derive), `_seed_population` upserts EVERY row before this guard and raises on
        # failure (so a missing row is not a cause either), and naming them sends
        # the operator to a dead end (`#13745` review rounds 5-6).
        assert "operator point" not in ei.value.message, ei.value.message
        assert "EMBED gate" not in ei.value.message, ei.value.message
        assert "swallowed" not in ei.value.message, ei.value.message
        assert "never reached the row" not in ei.value.message, ei.value.message
        # ⛔ THE COMPLETENESS OF THAT LIST IS PINNED AS A CODE FACT, NOT AS PROSE.
        # Two attempts to pin the MESSAGE structurally failed the same way: one
        # matched literals (a synonym passed), the next counted the word "row" (a
        # synonym without it passed, and an accurate edit that mentioned a row
        # false-REDDENED). Prose cannot be pinned this way (`#13745` reviews
        # rounds 6-7), and a pin that refuses a correct edit is a trap for the next
        # fixer, so the claim it was reaching for is asserted where it lives — on
        # the SEED (see `test_the_seed_writes_a_row_for_EVERY_entry` above): the
        # list is complete BECAUSE the seed writes every row and raises on failure.
        # This negative assertion stays only for the exact regressions we have
        # already shipped.
        # ⛔ AND IT MUST NOT PROMISE MORE THAN THE GUARD CAN SEE. It once said the
        # text pairing refuses a node that kept "an EARLIER text's vector" — which
        # the pairing cannot detect, because this run's text is written
        # unconditionally beside the preserved vector (`#13745` round 3, finding 1).
        assert "EARLIER text" not in ei.value.message, ei.value.message
        assert "which the text pairing" not in ei.value.message, ei.value.message

    def test_the_guard_does_not_CLAIM_vector_provenance(self, tool_mod):
        """The SUMMARY line is the contract a reader quotes; it must not overclaim.

        ⛔ `#13745` review round 4, finding 2: both docstrings opened with "THIS
        run's vector" while their own bodies said the count does NOT prove the
        stored vector is this run's (the fold preserves an old vector when the
        payload's is refused). Only the body was corrected in round 3, so `help()`
        and an IDE kept advertising the contract the guard does not enforce.
        """
        for fn in (tool_mod.graph_population_vectors, tool_mod.require_graph_population):
            summary = (fn.__doc__ or "").strip().splitlines()[0].lower()
            assert "this run's vector" not in summary, summary
            assert "this run's text" in summary, summary

    def test_the_message_reports_the_two_numbers_it_was_given(self, tool_mod):
        """⛔ THE ARITHMETIC IS PINNED, AND THE COUNT IS DESCRIBED AS WHAT IT IS.

        `matched` is the id-AND-text count, but the message used to report it as
        "the graph holds an EMBEDDING for N of M rows" — so in the one case where
        the halves disagree (the graph holds a vector for a row whose text is not
        this run's) the message named a cause that is not true and sent the
        operator to the embedder (`#13745` review round 10).
        """
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_graph_population(785, 786)
        msg = ei.value.message
        # the numbers the caller passed, not a recomputation
        assert "785" in msg and "786" in msg, msg
        # and the count is the PAIR, said so in the message itself
        assert "NOT A VECTOR COUNT" in msg, msg
        assert "THIS RUN'S TEXT" in msg, msg

    def test_guard_refuses_an_unmeasurable_graph(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_graph_population(None, 786)
        assert ei.value.code == "graph_population_unmeasurable"

    def test_guard_refuses_an_empty_graph(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_graph_population(0, 786)
        assert ei.value.code == "graph_population_absent"

    def test_guard_passes_a_fully_seeded_population(self, tool_mod):
        tool_mod.require_graph_population(786, 786)  # no raise

    def test_the_run_asks_the_graph_question_INSIDE_the_arm_loop(self):
        """Wired over the population's own rows, and gating `graph_dense` alone.

        ⛔ A guard nothing calls is the shape this finding started as. ⛔ AND THE
        SCOPE IS THE RECORD GUARD'S, NOT THE RUN'S (`#13745` review round 1,
        finding 2): a partly seeded graph degrades `graph_dense` ONLY — `lexical`
        forces `EmbeddingModel.get()` to None and reads a full-text index built
        from `n.content`, which the fold writes unconditionally, and
        `record_dense` reads Postgres. Refusing the run over it therefore throws
        away `record_dense_vs_lexical`, the lane's headline question, which is the
        failure the record gate 40 lines up was already moved out of.
        """
        tree = ast.parse(TOOL.read_text())
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        calls = [n for n in ast.walk(run) if isinstance(n, ast.Call)]
        named = [c for c in calls if isinstance(c.func, ast.Name)]
        guard = [c for c in named if c.func.id == "require_graph_population"]
        assert guard, "run_measurement never requires the graph's own cohort"
        # ⛔ ARG 2 MUST BE THE POPULATION'S OWN LENGTH — a constant makes the
        # guard a no-op.
        n_arg = guard[0].args[1]
        assert isinstance(n_arg, ast.Call), ast.dump(n_arg)[:120]
        assert isinstance(n_arg.func, ast.Name) and n_arg.func.id == "len", ast.dump(n_arg)[:120]
        assert isinstance(n_arg.args[0], ast.Name), ast.dump(n_arg)[:120]
        assert n_arg.args[0].id == "population_ids", ast.dump(n_arg)[:120]
        # ⛔ ARG 1 MUST BE THE ID-AND-TEXT MEASUREMENT, not the prefix-wide count
        # the run already holds — `require_graph_population(graph_vectors, …)` is
        # the exact bug this pin exists for.
        matched = guard[0].args[0]
        assert isinstance(matched, ast.Name), ast.dump(matched)[:120]
        assert matched.id == "graph_population", ast.dump(matched)[:120]
        bound = [
            n
            for n in ast.walk(run)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "graph_population" for t in n.targets)
        ]
        assert bound, "graph_population is never bound in run_measurement"
        value = bound[0].value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name), ast.dump(value)[
            :120
        ]
        assert value.func.id == "graph_population_vectors", ast.dump(value)[:120]
        asked = value.args[1]
        assert isinstance(asked, ast.Name) and asked.id == "population", ast.dump(asked)[:120]
        # ⛔ AND IT MUST SIT INSIDE THE ARMS LOOP, UNDER `arm == ARM_GRAPH_DENSE`.
        # At run level it refuses the two arms that need no graph vector at all;
        # under any other arm it either blocks the wrong one or runs the dense
        # arm on a cohort it never verified.
        arms_loop = next(
            n
            for n in ast.walk(run)
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "ARMS"
        )
        in_loop = [
            c
            for c in ast.walk(arms_loop)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "require_graph_population"
        ]
        assert in_loop, (
            "the graph guard must gate the `graph_dense` arm INSIDE the arms loop "
            "— at run level it blocks `lexical` and `record_dense` too, which need "
            "no graph vector, and discards the headline `record_dense_vs_lexical`"
        )
        parents = {}
        for node in ast.walk(run):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        node = in_loop[0]
        enclosures = []
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If):
                enclosures.append(node)
        assert any("ARM_GRAPH_DENSE" in ast.unparse(if_node.test) for if_node in enclosures), (
            "the guard must be enclosed in `if arm == ARM_GRAPH_DENSE` — inside the "
            "loop but ungated it blocks whichever arm is scored first"
        )
