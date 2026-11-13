"""#12297 — the user-search calibration harness's DECLARED query construction.

This file does not assert a recall number — the numbers live in the artifact and
in the calibration doc, where they cannot rot away from the evidence. What it
pins is the part a later reader would otherwise have to re-derive: **what the
query set actually is**, because every figure in that doc is a property of it.

  1. the two rules are the declared ones and nothing else (prefix / midspan);
  2. ``midspan`` is NOT a prefix — a reranker that only rewards prefix overlap
     must not be able to pass on the prefix case alone;
  3. a passage too short to yield a query yields NONE (never a truncated one);
  4. the harvest's exclusion rules are the declared ones (role, length, code),
     its order is deterministic, and it de-duplicates;
  5. the metric helpers decline to reward a gold that never entered the pool.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "rerank_calibration_12297", ROOT / "tools" / "rerank_calibration_12297.py")
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)

PASSAGE = (
    "The lane could not deliver because the credential it resolved was "
    "read-only for the repository, so every push was refused at the remote "
    "and the work stayed parked in the worktree. The remedy is to prove the "
    "write capability before the first hour of work rather than at the end "
    "of it, because a lane that discovers this late has already spent its "
    "budget and cannot deliver anything at all.")


# ── 1/2/3. the declared query construction ────────────────────────────────

def test_declared_rules_are_the_only_rules():
    q = cal.build_queries(PASSAGE, span=8)
    assert set(q) == set(cal.QUERY_RULES) == {"prefix", "midspan"}
    assert all(len(v.split()) == 8 for v in q.values())


def test_midspan_is_not_a_prefix():
    q = cal.build_queries(PASSAGE, span=8)
    assert q["prefix"] != q["midspan"]
    assert not q["midspan"].startswith(q["prefix"].split()[0] + " " +
                                       q["prefix"].split()[1])


def test_prefix_is_the_first_words_and_midspan_comes_from_the_middle():
    words = cal._WORD.findall(PASSAGE)
    q = cal.build_queries(PASSAGE, span=8)
    assert q["prefix"] == " ".join(words[:8])
    mid = (len(words) - 8) // 2
    assert q["midspan"] == " ".join(words[mid:mid + 8])
    # the middle really is away from both ends
    assert mid > 0 and mid + 8 < len(words)


def test_a_passage_too_short_yields_no_query():
    """Never a truncated query: a 2-word "query" is not the declared rule."""
    assert cal.build_queries("too short to have a middle span", span=8) == {}


def test_query_construction_is_deterministic():
    assert cal.build_queries(PASSAGE, 8) == cal.build_queries(PASSAGE, 8)


# ── 4. the harvest's exclusion rules, order and de-duplication ─────────────

def _record(role, content, sid="s1"):
    return {"session_id": sid, "conversation": [{"role": role,
                                                 "content": content}]}


@pytest.fixture
def corpus(tmp_path):
    long_text = PASSAGE
    rows = [
        _record("assistant", long_text, "s1"),
        _record("assistant", long_text, "s2"),            # duplicate content
        _record("user", long_text, "s3"),                 # wrong role
        _record("assistant", "short", "s4"),              # too short
        _record("assistant", "```python\nprint(1)\n```" + long_text, "s5"),
    ]
    p = tmp_path / "2026-11-01.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot-json\n")
    return tmp_path


def test_harvest_excludes_by_the_declared_rules(corpus):
    got = cal.harvest_passages(corpus, limit=50)
    # exactly ONE survives: the duplicate, the wrong role, the short one and the
    # code-fenced one are all excluded
    assert len(got) == 1, [r["content"][:40] for r in got]
    assert got[0]["session_id"] in ("s1", "s2")


def test_harvest_order_is_deterministic_and_capped(corpus, tmp_path):
    a = cal.harvest_passages(corpus, limit=50)
    b = cal.harvest_passages(corpus, limit=50)
    assert [r["content"] for r in a] == [r["content"] for r in b]
    assert len(cal.harvest_passages(corpus, limit=1)) == 1


def test_harvest_tolerates_an_absent_directory(tmp_path):
    assert cal.harvest_passages(tmp_path / "nope", limit=10) == []


# ── 5. the metric helpers do not reward absence ────────────────────────────

def test_ndcg_and_rr_reward_only_a_rank_inside_the_cut():
    assert cal._ndcg(None) == 0.0            # never in the pool
    assert cal._ndcg(cal.CUT) == 0.0         # present but past the cut
    assert cal._ndcg(0) == 1.0
    assert cal._ndcg(1) == pytest.approx(1 / 1.5849625007211563)
    assert cal._rr(None) == 0.0
    assert cal._rr(cal.CUT) == 0.0
    assert cal._rr(0) == 1.0 and cal._rr(1) == 0.5


def test_ncdg_is_monotonically_decreasing_in_rank():
    vals = [cal._ndcg(r) for r in range(cal.CUT + 2)]
    assert vals[:cal.CUT] == sorted(vals[:cal.CUT], reverse=True)
    assert all(v > 0 for v in vals[:cal.CUT])
    assert vals[cal.CUT] == 0.0 and vals[cal.CUT + 1] == 0.0


def test_the_default_pool_is_the_wired_default():
    """The measured configuration must be the SHIPPED one, not a second one."""
    from tortoise import rerank
    assert cal.POOL == rerank.DEFAULT_FUSED_RERANK_POOL


def test_the_briefs_cost_table_is_the_artifacts_and_names_the_admitted_range():
    """§5's two cells are the artifacts' figures, and its range is the harness's.

    ⛔ ROW-SCOPED, not document-wide: ``5.179`` and ``85.227`` each appear in
    several places in the brief, so ``f"{ms}" in text`` stayed green with BOTH
    §5 cells rewritten to other runs' figures (measured — 5.179 → 5.463, the
    lexical-control run's number, and 85.227 → 62.900). Each figure is asserted
    in the row that names its own artifact, which is the binding the §8 test
    already uses.

    ⛔ And the range is DERIVED: ``MIN_CHARS``/``MAX_CHARS`` are named constants,
    and the word floor is ``MIN_WORDS`` — a restated ``40`` here could not see
    the harvest move.
    """
    doc = ROOT / "docs" / "research" / "2026-11-12-12297-rerank-calibration"
    text = (doc / "research-brief.md").read_text()
    noun = json.loads((doc / "measurement-noun-path.json").read_text())
    user = json.loads((doc / "measurement-user-search.json").read_text())

    # ⛔ Scoped to §5 before collecting the rows: with every ``|``-line in the
    # document, ``next(row for row in rows if artifact_name in row)`` bound to the
    # first inventory-style row naming the artifact anywhere — adding one in §2
    # let BOTH §5 cells be rewritten to other runs' figures with the test green
    # (measured).
    assert "## 5." in text, "§5's heading is gone — cannot scope the cost table"
    section = text.split("## 5.", 1)[1].split("\n## ", 1)[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("|")]
    for art, artifact_name, label in (
            (noun, "measurement-noun-path.json", "noun-path"),
            (user, "measurement-user-search.json", "user-search")):
        ms = (art["latency"]["ms_per_pair"] if "latency" in art
              else art["arms"]["rerank"]["latency"]["ms_per_pair"])
        row = next((r for r in rows if artifact_name in r), "")
        assert row, f"§5 has no cost row naming {artifact_name}"
        assert f"{ms:.3f}" in row, (
            f"§5's {label} row does not carry that artifact's ms/pair ({ms}): "
            f"{row}")

    assert f"{cal.MIN_CHARS}–{cal.MAX_CHARS} chars" in text, (
        "§5 must name the harness's own char admission range")
    # …scoped to the §5 cell itself: the brief legitimately discusses 2/4/8-word
    # spans elsewhere, so a document-wide sweep would read those as cost cells.
    cell = next((ln for ln in rows if "passage-length" in ln), "")
    assert cell, "§5's passage-length cost cell is gone"
    floor = min(int(w) for w in re.findall(r"(\d+)\s*words", cell))
    # ⛔ EQUALITY, both directions: `floor >= MIN_WORDS` caught a RAISED harvest
    # floor but stayed green when it was LOWERED to 25 — the direction that
    # makes the brief overstate the population the cost figure describes.
    assert floor == cal.MIN_WORDS, (
        f"§5's passage-length cell states {floor} words; the harvest's floor is "
        f"{cal.MIN_WORDS} — the cell must state the harvest's own floor: {cell}")


# ── 6. the run refuses a baseline that is not the fused order ──────────────

def _run_ns(**over):
    """The minimum Namespace `run` touches before its first refusal."""
    ns = dict(sessions_dir="~/.tortoise/session-events", records=1, span=8,
              limit=cal.POOL, pool_size=None, model=None, threads=1,
              namespace="x", graph=None, per_query=False,
              no_assert_permutation=False, allow_reranked_baseline=False,
              out=None)
    ns.update(over)
    return type("NS", (), ns)()


def test_the_run_measures_the_model_the_PRODUCT_would_load(monkeypatch):
    """The artifact's ``model`` field must name what was actually timed.

    ⛔ Pinned BEHAVIOURALLY and at the WIRING. A source grep is satisfied by this
    file's own prose, and a test of ``resolve_arm_model`` alone is satisfied by a
    hard-coded id at the call site (measured in the sibling tool: all 43 tests
    green over exactly that mutation). The shipped read resolves through
    ``rerank_pool`` → ``load_scorer(model or fused_rerank_model())``, so
    ``TORTOISE_FUSED_RERANK_MODEL`` decides which cross-encoder the product
    loads; reverting to ``RERANK_MODEL_DEFAULT`` would have the artifact misname
    the model behind its own ``ms_per_pair``.
    """
    from tortoise import rerank

    monkeypatch.setattr(rerank, "fused_rerank_model",
                        lambda: "cross-encoder/from-the-knob")
    assert cal.resolve_arm_model(_run_ns()) == "cross-encoder/from-the-knob"
    assert cal.resolve_arm_model(_run_ns(model="cross-encoder/pinned")) == (
        "cross-encoder/pinned")

    # …and the timed call must be HANDED that id. Behavioural: the seam is
    # driven with the real ``rerank_pool`` monkeypatched, so no prose can satisfy
    # it — the previous version grepped ``run``'s source, and a docstring naming
    # the identifiers passed it while the timed call used another model.
    from tortoise import rerank as _r
    seen: dict = {}
    monkeypatch.setattr(_r, "rerank_pool",
                        lambda q, rows, **kw: (seen.update(kw), (rows, {}))[1])
    cal.score_pool("q", [{"id": "a"}], model="cross-encoder/pinned-by-args",
                   limit=cal.POOL)
    assert seen.get("model") == "cross-encoder/pinned-by-args", seen
    assert seen.get("enabled") is True and seen.get("pool") == cal.POOL, seen

    # …and `run` must hand BOTH calls the same NAME, read from the AST so a
    # comment, a docstring or a string literal cannot satisfy it.
    tree = ast.parse(inspect.getsource(cal.run).lstrip())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

    def _kw(call, name):
        return next((k.value for k in call.keywords if k.arg == name), None)

    loader = [c for c in calls
              if isinstance(c.func, ast.Name) and c.func.id == "get_scorer"]
    timed = [c for c in calls
             if isinstance(c.func, ast.Name) and c.func.id == "score_pool"]
    assert loader and timed, "the run must load and time through these calls"
    assert isinstance(loader[0].args[0], ast.Name) and \
        loader[0].args[0].id == "model", ast.dump(loader[0])
    handed = _kw(timed[0], "model")
    assert isinstance(handed, ast.Name) and handed.id == "model", ast.dump(timed[0])


def test_a_degraded_arm_is_refused_and_says_so():
    """A degrade is a PERMUTATION, so every other check in the loop passes it.

    `rerank_pool` returns `applied: False` (the order untouched) on a score
    exception, a length-mismatched score vector, a non-numeric or non-finite
    score. The arm would then report gained 0 / lost 0 with identical nDCG —
    indistinguishable in the artifact from a real zero — while the permutation
    asserts stay green, because a degraded rerank IS a permutation. The scorer
    LOADING successfully proves nothing about whether it scored.

    ⛔ The pure function is pinned here because the run path needs a store before
    it can reach a real degrade; the run's own call is pinned in the test below.
    """
    assert cal.rerank_degrade_refusal({"applied": True, "scored": 40}) is None

    # ⛔ A READ-side skip is NOT a degrade: ``rerank_pool`` returns
    # ``applied: False`` for ``empty_pool`` / ``empty_query`` / ``disabled``
    # BEFORE the scorer is consulted, and the instrument already models that
    # outcome (gold stays in the denominator, ``_ndcg(None)`` = 0, counted as
    # ``pool_absent``). Refusing on it would abort a ~46-minute run over one
    # query whose derived text matched nothing — and blame the reranker.
    for read_side in sorted(cal.READ_SIDE_DEGRADES):
        assert cal.rerank_degrade_refusal(
            {"applied": False, "degrade_reason": read_side}) is None, read_side
    # ⛔ AND THE GATE STATE IS *NOT* ONE OF THEM. ``"disabled"`` is not a read
    # outcome: ``score_pool`` forces ``enabled=True`` so it is unreachable today,
    # and tolerating it made the guard FAIL OPEN the day that call site stopped
    # forcing the gate — every query of a gate-off run would be counted as a read
    # skip and the artifact would ship unmixed before-only figures unrefused.
    assert cal.rerank_degrade_refusal(
        {"applied": False, "degrade_reason": "disabled"}) is not None, (
        "a gate-off run must be refused, not counted as a read-side skip")
    # …and the read-side skip must still be COUNTED, or the tolerance becomes a
    # silent hole. Pinned on the pure helper: "the increment statement exists"
    # stayed green when the guard was inverted so the counter could never fire
    # (measured).
    block = cal._arm_block()
    for read_side in sorted(cal.READ_SIDE_DEGRADES):
        cal.record_stats(block, {"applied": False, "degrade_reason": read_side})
    assert block["rerank_skipped_read"] == len(cal.READ_SIDE_DEGRADES), block
    # an APPLIED run is not a skip, and a scorer degrade is refused (so it must
    # not be counted as one either — it would hide inside the skip tally).
    cal.record_stats(block, {"applied": True, "degrade_reason": "ok"})
    cal.record_stats(block, {"applied": False, "degrade_reason": "non-finite scores"})
    assert block["rerank_skipped_read"] == len(cal.READ_SIDE_DEGRADES), block

    msg = cal.rerank_degrade_refusal(
        {"applied": False, "degrade_reason": "score_exception", "scored": 0})
    assert msg is not None
    assert msg.startswith("REFUSED:"), msg
    assert "score_exception" in msg, msg
    # …and a degrade with no reason at all is still refused, naming that.
    bare = cal.rerank_degrade_refusal({"applied": False})
    assert bare is not None and "no reason given" in bare, bare


def test_the_applied_invariant_is_DERIVED_from_the_skips():
    """`rerank_applied_on_every_query` must be computed, not asserted.

    ⛔ Tolerating a read-side skip made the old literal false BY CONSTRUCTION: a
    finished run can count `rerank_skipped_read > 0` beside
    `rerank_applied_on_every_query: True` in the same artifact, and a run that
    skipped EVERY query would still have claimed the reranker ran on all of
    them. The report now derives both fields from the arms.
    """
    import inspect
    src = inspect.getsource(cal.run)
    assert '"rerank_applied_on_every_query": _skipped == 0' in src, (
        "the invariant must be derived from the skip tally")
    assert '_skipped = sum(b["rerank_skipped_read"] for b in arms.values())' in src
    # …and a run that scored nothing must refuse rather than emit null latency.
    assert "if not pairs_scored:" in src, (
        "a run with no scored pair has no latency to report — it must refuse")
    taper = src[src.index("if not pairs_scored:"):]
    assert "return 3" in taper, (
        "the no-pairs refusal must END the run, not warn")


def test_the_run_refuses_on_the_degrade_it_detects():
    """The loop must actually CALL the refusal — the helper returning a string
    is worth nothing if `run` prints it and keeps measuring."""
    import inspect
    src = inspect.getsource(cal.run)
    assert "_refusal = rerank_degrade_refusal(stats)" in src, (
        "the arm no longer consults the degrade refusal")
    assert "return 3" in src[src.index("_refusal = rerank_degrade_refusal"):], (
        "the degrade refusal must END the run, not warn")


def test_an_armed_read_gate_refuses_the_run(monkeypatch, capsys):
    """The read's OWN gate decides what "before" IS.

    The arm reranks explicitly; if `TORTOISE_FUSED_RERANK` is already armed then
    the read reranked the pool first, so `before` and `after` are two rerank
    passes and the result is a gained/lost 0 that is indistinguishable from a
    real zero. That is the one wrong answer this instrument must never produce,
    and it needs no store to detect — so it is refused before the DB check.

    ⛔ The STDERR assertion is the discriminating half: the store refusal ALSO
    exits 3, and this test unsets the URI, so an exit-code-only assertion passes
    with the gate refusal deleted (measured by mutation). Asserting the message
    names the GATE is what makes this test able to fail.
    """
    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    assert cal.run(_run_ns()) == 3
    err = capsys.readouterr().err
    assert "TORTOISE_FUSED_RERANK" in err
    assert "TORTOISE_DB_URI" not in err      # the gate fired, not the store


def test_an_armed_gate_is_measurable_only_when_asked_for(monkeypatch, capsys):
    """`--allow-reranked-baseline` opts in deliberately; then the refusal that
    fires is the STORE one, which is how this test tells the two apart without a
    DB."""
    monkeypatch.setenv("TORTOISE_FUSED_RERANK", "1")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    assert cal.run(_run_ns(allow_reranked_baseline=True)) == 3
    assert "TORTOISE_DB_URI" in capsys.readouterr().err
