"""#2976 — the admission-arms instrument (hermetic: no DB, no reader, no judge).

`tools/longmem_eval/temporal_admission_arms.py` exists because the harness runs
ONE configuration per invocation and its ingest-reuse seam is v2-only, so a
3-arm before/after would pay the ingest three times for a difference that lives
entirely in admission/rendering. The driver runs every arm over ONE ingest.

These tests pin the properties that make its numbers trustworthy:

* the arms differ ONLY in the two #2976 switches (a delta cannot come from an
  unrelated config change), and the baseline still pins ``tr_top_k=12``;
* the shared knobs are the #2578 A-default configuration;
* the gold census counts marked hits in the window and in the pool (the
  "retrieved but not admitted" distinction the issue turns on);
* shard assignment partitions the subset disjointly and is order-independent;
* the summary aggregates per arm and per class from the row schema — and
  excludes the abstention controls from the answerable denominator.

Run: TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/longmem_eval/test_temporal_admission_arms.py -q
"""
from __future__ import annotations

import json

from tools.longmem_eval import temporal_admission_arms as arms


def test_arms_differ_only_in_the_2976_switches():
    base = arms.arm_by_id("baseline")
    # EXPLICIT off — an empty dict would inherit the ambient env and collapse
    # the three arms into one configuration.
    assert base["knobs"] == {"tr_session_admission": False,
                             "tr_session_completion": False}
    adm = arms.arm_by_id("admission")
    # BOTH switches explicit: an arm naming only the switch under test leaves
    # the other one to the ambient env, so a shell carrying
    # `TORTOISE_LME_TR_SESSION_COMPLETION=1` would make `admission` identical
    # to `admission+completion`.
    assert adm["knobs"] == {"tr_session_admission": True,
                            "tr_session_completion": False}
    comp = arms.arm_by_id("admission+completion")
    assert comp["knobs"] == {"tr_session_admission": True,
                             "tr_session_completion": True}
    # every arm resolves BOTH switches, so no arm's configuration can depend
    # on the ambient env
    for arm in arms.ARMS:
        assert set(arm["knobs"]) == {"tr_session_admission",
                                     "tr_session_completion"}, (
            f"{arm['id']} leaves a #2976 switch to the ambient env")
    # …and the three arms are three DIFFERENT configurations, so a delta
    # between any two of them is attributable to exactly the switch that
    # differs
    assert len({json.dumps(a["knobs"], sort_keys=True)
                for a in arms.ARMS}) == len(arms.ARMS)
    for arm in arms.ARMS:
        assert not (set(arm["knobs"]) & set(arms.SHARED_KNOBS)), (
            f"{arm['id']} re-specifies a shared knob — the delta would stop "
            "being attributable to admission")


def test_shared_knobs_are_the_2578_baseline_configuration():
    assert arms.SHARED_KNOBS["tr_top_k"] == 12      # the R5 flood cap
    assert arms.SHARED_KNOBS["max_context_tokens"] == 8000
    assert arms.SHARED_KNOBS["max_chunks_per_session"] == 3
    assert tuple(arms.SHARED_KNOBS["ks"]) == (5, 10, 20)


def test_gold_census_separates_admitted_from_retrieved():
    pool = [
        {"id": "a", "has_answer": True, "session_id": "s1"},
        {"id": "b", "has_answer": True, "session_id": "s2"},
        {"id": "c", "has_answer": False, "session_id": "s1"},
    ]
    window = [pool[0]]
    census = arms.gold_census(window, pool)
    assert census == {"gold_admitted": 1, "gold_in_pool": 2,
                      "context_items": 1, "context_sessions": 1}


def test_shard_assignment_is_disjoint_and_stable():
    qids = [f"q{i}" for i in range(200)]
    buckets = {i: {q for q in qids if arms.shard_of(q, 7) == i}
               for i in range(7)}
    all_assigned = set().union(*buckets.values())
    assert all_assigned == set(qids)
    assert sum(len(v) for v in buckets.values()) == len(qids)  # disjoint
    assert arms.shard_of("q42", 12) == arms.shard_of("q42", 12)


def test_summary_reports_per_arm_per_class_and_excludes_controls(tmp_path):
    rows = [
        # an interval question: baseline misses, admission converts
        {"qid": "a", "arm": "baseline", "cls": "interval", "answerable": True,
         "label": False, "reader_refusal": True, "gold_admitted": 0,
         "context_tokens": 97, "seconds": 1.0},
        {"qid": "a", "arm": "admission", "cls": "interval", "answerable": True,
         "label": True, "reader_refusal": False, "gold_admitted": 1,
         "context_tokens": 4000, "seconds": 2.0},
        # an ordering/compare question that both miss
        {"qid": "b", "arm": "baseline", "cls": "ordering/compare",
         "answerable": True, "label": False, "reader_refusal": True,
         "gold_admitted": 0, "context_tokens": 200, "seconds": 1.0},
        {"qid": "b", "arm": "admission", "cls": "ordering/compare",
         "answerable": True, "label": False, "reader_refusal": True,
         "gold_admitted": 0, "context_tokens": 5000, "seconds": 2.0},
        # an abstention control: correct refusals are NOT part of the
        # answerable denominator
        {"qid": "c_abs", "arm": "baseline", "cls": "current-state",
         "answerable": False, "label": True, "reader_refusal": True,
         "gold_admitted": 0, "context_tokens": 100, "seconds": 1.0},
        {"qid": "c_abs", "arm": "admission", "cls": "current-state",
         "answerable": False, "label": True, "reader_refusal": True,
         "gold_admitted": 0, "context_tokens": 100, "seconds": 1.0},
    ]
    src = tmp_path / "rows.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    dst = tmp_path / "sum.json"
    summary = arms.summarize(src, dst)

    base = summary["arms"]["baseline"]
    adm = summary["arms"]["admission"]
    assert base["n"] == adm["n"] == 2          # controls excluded
    assert base["correct"] == 0 and adm["correct"] == 1
    assert base["refusals"] == 2 and adm["refusals"] == 1
    assert base["gold_admitted"] == 0 and adm["gold_admitted"] == 1
    assert set(base["by_class"]) == {"interval", "ordering/compare"}
    assert adm["by_class"]["interval"] == {"n": 1, "correct": 1}
    assert dst.exists() and json.loads(dst.read_text())["arms"]["admission"]


def test_rows_and_summary_carry_the_declared_grader_identity(tmp_path):
    """The #2976 run is a DECLARED substitution (pinned OpenRouter route and the
    official judge were both blocked). A row that does not name its grader can
    never be distinguished from one measured under the official judge, so the
    identity is recorded per row and lifted into the summary provenance."""
    rows = [
        {"qid": "a", "arm": "baseline", "cls": "interval", "answerable": True,
         "label": True, "reader_refusal": False, "gold_admitted": 1,
         "context_tokens": 100, "seconds": 1.0,
         "reader_model": "deepseek-v4-flash",
         "reader_model_spec": "deepseek:deepseek-v4-flash",
         "reader_pinned": False,
         "judge_model": "variant-containment",
         "judge_model_spec": "variant-containment"},
    ]
    src = tmp_path / "rows.jsonl"
    src.write_text(json.dumps(rows[0]) + "\n")
    out = tmp_path / "sum.json"
    summary = arms.summarize(src, out)
    prov = summary["provenance"]
    assert prov["reader_model_spec"] == "deepseek:deepseek-v4-flash"
    assert prov["reader_pinned"] is False
    assert prov["judge_model_spec"] == "variant-containment"


# ── the eval wiring (finding: the arms had no wiring test) ─────────────────


def test_completion_requires_admission_loudly():
    """A completion pass under a still-binding item cap measures nothing, so the
    pairing is refused at arm resolution rather than run as a silent no-op."""
    import pytest

    from tools.longmem_eval.retrieve import retrieve_for_question

    with pytest.raises(ValueError, match="tr_session_completion requires"):
        retrieve_for_question(
            object(), {"question_id": "q", "question": "how many days?",
                       "question_type": "temporal-reasoning"},
            tr_session_completion=True, tr_session_admission=False)


def test_admission_on_selects_the_session_assembler_without_an_item_cap(
        monkeypatch):
    """THE SHIPPED WIRING: `tr_session_admission` ON must route the reader
    window through `assemble_session_context` with NO item cap (the item cap IS
    the defect), and OFF must leave the historical `assemble_context(...
    context_item_cap=tr_top_k)` untouched.

    Nothing else pins the selection: if a later change passed
    ``context_item_cap=eff_item_cap`` into the session assembler, or inverted
    the ``if``, every other test stays green while the arm silently measures
    the OLD 12-item window — the exact 0/52-class regression #2976 exists to
    fix. Hermetic: the pool comes from a stubbed ``hybrid_search`` and the
    graph double answers ``[]``, so no DB, no reader, no judge.
    """
    import types

    from tools.longmem_eval import retrieve as R

    class _Empty:
        result_set: list = []

    class _Graph:
        def query(self, cypher, params=None):
            del cypher, params
            return _Empty()

    class _Sdk:
        def _get_proj(self):
            return types.SimpleNamespace(g=_Graph())

    calls: dict[str, tuple] = {}

    def _session_assembler(*args, **kwargs):
        calls["session"] = (args, kwargs)
        return []

    def _item_capped_assembler(*args, **kwargs):
        calls["item_capped"] = (args, kwargs)
        return []

    monkeypatch.setattr(R, "_assemble_session_context", _session_assembler)
    monkeypatch.setattr(R, "_assemble_context", _item_capped_assembler)
    monkeypatch.setattr(R, "hybrid_search", lambda *a, **kw: [])

    question = {"question_id": "q1", "question": "how many days passed?",
                "question_type": "temporal-reasoning",
                "question_date": "2023-06-01", "haystack_dates": [],
                "haystack_sessions": [], "answer": "5"}

    ret = R.retrieve_for_question(_Sdk(), question, ks=(5,), top_k=20,
                                  tr_top_k=12, tr_session_admission=True)
    assert set(calls) == {"session"}, (
        f"admission ON must use ONLY the session assembler, got {sorted(calls)}")
    _args, kwargs = calls["session"]
    assert "item_cap" not in kwargs, (
        "the item cap is the defect — the session assembler must not receive it")
    assert ret["tr_session_admission_stats"]["applied"] is True

    # …and OFF keeps the historical item-capped assembler, unchanged.
    calls.clear()
    ret_off = R.retrieve_for_question(_Sdk(), question, ks=(5,), top_k=20,
                                      tr_top_k=12,
                                      tr_session_admission=False)
    assert set(calls) == {"item_capped"}, (
        f"admission OFF must keep the item-capped assembler, got {sorted(calls)}")
    _args, off_kwargs = calls["item_capped"]
    assert off_kwargs["context_item_cap"] == 12, (
        "TR keeps its pinned tr_top_k item cap when the arm is OFF")
    assert ret_off["tr_session_admission_stats"]["applied"] is False


def test_completion_fetch_failure_keeps_the_original_pool(monkeypatch):
    """Fail-open: a broken graph fetch must leave the pool (and therefore the
    measured window) exactly as retrieved — never a partial splice, never an
    exception into the measurement."""
    from tools.longmem_eval import retrieve as R

    class _Boom:
        def _get_proj(self):
            raise RuntimeError("graph unreachable")

    pool = [{"id": "a", "content": "x", "session_id": "s1"},
            {"id": "b", "content": "y", "session_id": "s2"}]
    out, report = R._tr_complete_sessions(
        _Boom(), pool, ["2023-01-01"], seed_window=1, seed_sessions=1,
        total_cap=1, per_session_cap=1)
    assert out == pool
    assert report["ok"] is False
    assert report["injected"] == 0


def test_fingerprint_stamps_the_arm_and_its_budgets_only_when_on(monkeypatch):
    """A completion budget changes what the reader sees, so it must gate resume:
    a TOTAL=20 checkpoint must not resume under TOTAL=60 (the
    `temporal_leg_limit` precedent). OFF must leave both keys ABSENT so the
    default fingerprint stays byte-identical."""
    from tools.longmem_eval.run import _build_fingerprint

    base = dict(reader_model="r", judge_model="j", ks=(5,), top_k=20,
                split="s", ingest_mode="deterministic",
                extractor_model=None, max_retries=1,
                dataset_fingerprint="d", rerank_config={})

    def fp(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return _build_fingerprint(**base)

    off = fp()
    assert "tr_session_admission" not in off
    assert "tr_session_completion_total" not in off

    on = fp(TORTOISE_LME_TR_SESSION_ADMISSION="1",
            TORTOISE_LME_TR_SESSION_COMPLETION="1")
    assert on["tr_session_admission"] is True
    assert on["tr_session_completion"] is True

    monkeypatch.setenv("TORTOISE_LME_TR_SESSION_COMPLETION_TOTAL", "20")
    capped = _build_fingerprint(**base)
    assert capped["tr_session_completion_total"] == 20
    assert capped != on, "a budget change must change the fingerprint"
