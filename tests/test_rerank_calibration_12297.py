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

import importlib.util
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


# ── 7. the RUN, driven end to end over fakes ───────────────────────────────
#
# ⛔ Every claim this file made about `run`'s WIRING was, for three review rounds,
# a substring search over `inspect.getsource(cal.run)` — and each version was
# satisfied by the wrong thing: a docstring naming the identifiers, a `return 3`
# belonging to a LATER refusal, or a statement that existed but could never fire
# (all measured). A drive is the only pin that has no such neighbour: it asserts
# the EXIT CODE and the ARTIFACT, which is what the operator gets.

PASSAGE_TEXT = (
    "The lane could not deliver because the credential it resolved was read-only "
    "for the repository, so every push was refused at the remote and the work "
    "stayed parked in the worktree. The remedy is to prove the write capability "
    "before the first hour of work rather than at the end of it, because a lane "
    "that discovers this late has already spent its budget and cannot deliver "
    "anything at all. This branch measured the same failure twice more, which is "
    "why the rule now lives in the instruction file rather than in a comment.")


class _FakeGraph:
    """A real in-memory store: CREATE lands, and the reads see what landed.

    ⛔ The first version answered the landing check from a CONSTANT, so deleting
    the `CREATE` in `run` stayed green (measured) and the "did not land" refusal
    was exercised by nothing — while a benign id-scheme rename reddened all four
    drive tests, because the pin was bound to the fake's literal rather than to
    the write. A dict makes the check derive from the writes.
    """

    def __init__(self, drop_write: bool = False):
        self.landed: set[str] = set()
        self.drop_write = drop_write
        self.rows = [["rp-12297000000", "first"], ["rp-12297000001", "second"]]

    def query(self, cypher, params=None):
        """Answer by PARAMS, never by Cypher spelling.

        ⛔ The first version matched substrings of the query text, so the pin was
        bound to the fake's copy of the Cypher rather than to what the run MEANT:
        a benign rename ("STARTS WITH" → a scoped IN) would red every drive test
        while the write it guards still worked — and answering an unexpected
        query from a constant is the shape that let a deleted ``CREATE`` stay
        green. Params carry the intent; an unrecognised call is an ERROR here.
        """
        params = params or {}
        if "ids" in params:
            return self._result([[i] for i in list(params["ids"])
                                 if i in self.landed])
        if "id" in params:
            if not self.drop_write:
                self.landed.add(params["id"])
            return self._result([])
        raise AssertionError(
            f"the fake store got a query it does not model: {cypher!r} {params!r}")

    @staticmethod
    def _result(rows):
        class _R:
            result_set = rows
        return _R()


class _FakeProj:
    def __init__(self, drop_write: bool = False):
        self.g = _FakeGraph(drop_write)


class _FakeSDK:
    """Reads return the harvested ids in fused order; writes land in _FakeGraph."""

    drop_write = False

    def __init__(self, *a, **kw):
        self.proj = _FakeProj(type(self).drop_write)
        self.rows = self.proj.g.rows

    def _get_proj(self):
        return self.proj

    def tortoise_fts_query(self, query, **kw):
        return [{"id": r[0]} for r in self.rows]

    def close(self):
        pass


@pytest.fixture
def drive(monkeypatch, tmp_path, capsys):
    """Run the harness with a fake store and a stubbed scorer.

    Returns ``(exit_code, artifact_or_None, stderr)``. ``stats_for`` decides what
    the timed call reports, so a degrade / read-side skip / healthy pass is one
    line in the test instead of a monkeypatched source string.
    """
    from tortoise import rerank as rr
    from tortoise import sdk as sdk_mod

    def _go(*, stats_for=None, model=lambda: "cross-encoder/from-the-knob",
            arg_model=None, drop_write=False, **over):
        calls: list[dict] = []
        loaded: list[str] = []

        def _pool(query, rows, **kw):
            calls.append(kw)
            stats = stats_for(len(calls)) if stats_for else {
                "applied": True, "scored": len(rows)}
            return rows, dict(stats)

        def _scorer(m):
            # ⛔ RECORD the id the load check validates: without it, a run that
            # loads one model and times another passes every artefact assertion.
            loaded.append(m)
            return object(), "ok"

        monkeypatch.setattr(rr, "rerank_pool", _pool)
        monkeypatch.setattr(rr, "get_scorer", _scorer)
        monkeypatch.setattr(rr, "fused_rerank_model", model)
        monkeypatch.setattr(rr, "rerank_pool_enabled", lambda: False)
        monkeypatch.setattr(sdk_mod, "TortoiseSDK", _FakeSDK)
        monkeypatch.setattr(cal, "harvest_passages",
                            lambda *a, **k: [{"id": "x", "content": PASSAGE_TEXT}])
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
        out = tmp_path / "artifact.json"
        # A store that silently accepts the CREATE and keeps nothing — the
        # partial-write shape the landing check exists to catch.
        monkeypatch.setattr(_FakeSDK, "drop_write", drop_write)
        code = cal.run(_run_ns(threads=0, out=str(out), model=arg_model, **over))
        art = json.loads(out.read_text()) if out.exists() else None
        return type("Run", (), {"code": code, "art": art,
                                "err": capsys.readouterr().err,
                                "calls": calls, "loaded": loaded})()

    return _go


def test_a_healthy_run_records_the_resolved_model_and_no_skips(drive):
    """Exit 0, and the artifact names the model BOTH calls were handed."""
    run = drive()
    assert run.code == 0, run.err
    assert run.art is not None, "a clean run must emit its artifact"
    assert run.art["config"]["model"] == "cross-encoder/from-the-knob", run.art["config"]
    assert run.art["config"]["rerank_applied_on_every_query"] is True
    assert run.art["config"]["rerank_skipped_read"] == 0
    assert run.art["latency"]["pairs_scored"] > 0
    # the timed call was handed that SAME id — the binding, not a neighbour of it
    assert run.calls and all(c["model"] == "cross-encoder/from-the-knob"
                             for c in run.calls), run.calls


def test_an_EXPLICIT_model_pins_both_calls(drive):
    """``--model`` must reach the load check AND the timed call.

    ⛔ The drive otherwise only exercises ``args.model is None``, where the flag
    and the resolver agree by construction — so `model = fused_rerank_model()`
    and `get_scorer(fused_rerank_model())` both stayed green (measured), i.e. the
    flag could be silently ignored and the artifact would record the knob's id
    beside ``model_was_explicit: True``.
    """
    run = drive(model=lambda: "cross-encoder/from-the-knob",
                arg_model="cross-encoder/from-the-args")
    assert run.code == 0, run.err
    assert run.art["config"]["model"] == "cross-encoder/from-the-args", (
        run.art["config"])
    assert run.loaded == ["cross-encoder/from-the-args"], (
        f"the load check validated {run.loaded!r}, not the flag's model")
    assert all(c["model"] == "cross-encoder/from-the-args" for c in run.calls), (
        run.calls)


def test_a_DEGRADED_query_ends_the_run_and_leaves_no_artifact(drive):
    """The refusal must FIRE: exit 3 and nothing written.

    ⛔ Only the FIRST query degrades, so a refusal that fails to return lets the
    later queries score and the run COMPLETE — exit 0 with an artifact — which is
    what this asserts against. (Not "so `pairs_scored > 0`": the refusal returns
    BEFORE the increment, so `pairs_scored` is 0 at that moment either way. The
    discriminating signals are the exit code and the absent artifact.) Measured
    against the previous source-text pins, which stayed green when the `return 3`
    (or the whole `if`) was deleted while the artifact went on to report a
    gained/lost 0 indistinguishable from a real zero.
    """
    run = drive(
        stats_for=lambda n: ({"applied": False, "degrade_reason": "non-finite "
                              "scores", "scored": 0} if n == 1 else
                             {"applied": True, "scored": 2}))
    assert run.code == 3, (run.code, run.err)
    assert run.art is None, "a refused run must not leave an artifact behind"
    assert "REFUSED" in run.err and "non-finite scores" in run.err, run.err


def test_a_READ_side_skip_is_counted_and_disclosed_in_the_artifact(drive):
    """A tolerated skip must still show up, and every other query must score."""
    run = drive(
        stats_for=lambda n: ({"applied": False, "degrade_reason": "empty_pool",
                              "scored": 0} if n == 1 else
                             {"applied": True, "scored": 2}))
    assert run.code == 0, run.err
    assert run.art["config"]["rerank_skipped_read"] == 1, run.art["config"]
    assert run.art["config"]["rerank_applied_on_every_query"] is False
    assert sum(b["rerank_skipped_read"] for b in run.art["arms"].values()) == 1


def test_a_run_that_scores_NOTHING_refuses(drive):
    """No scored pair means no latency: refuse rather than publish a null."""
    run = drive(
        stats_for=lambda n: {"applied": False, "degrade_reason": "empty_pool",
                             "scored": 0})
    assert run.code == 3, (run.code, run.err)
    assert run.art is None
    assert "no pair was ever scored" in run.err, run.err


def test_the_wired_width_in_the_artifact_is_the_RESOLVED_one(drive, monkeypatch):
    """``pool_scored_is_wired_default`` must compare against the RESOLVED width.

    ⛔ The wired read resolves through ``rerank.fused_rerank_pool_width()``, env
    knob first. Comparing ``args.limit`` to the CONSTANT reports "this is the
    wired configuration" for a run the product would score at a different width —
    the same class as naming a model the product would not load.
    """
    monkeypatch.setenv("TORTOISE_FUSED_RERANK_POOL", "15")
    run = drive()
    assert run.code == 0, run.err
    assert run.art["config"]["wired_pool_width"] == 15, run.art["config"]
    assert run.art["config"]["pool_scored_is_wired_default"] is False, (
        run.art["config"])
    assert run.art["config"]["pool_scored"] == cal.POOL


def test_a_passage_that_did_not_land_refuses(drive):
    """The landing check must be able to fail, and to fail on ITS OWN evidence.

    ⛔ The first fake answered this check from a CONSTANT, so deleting the
    ``CREATE`` in ``run`` — or the whole landing check — stayed green (measured).
    The store now records writes, so the check reads what actually landed.
    """
    run = drive(drop_write=True)
    assert run.code == 3, (run.code, run.err)
    assert run.art is None
    assert "did not land" in run.err, run.err


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

    # ⛔ The BINDING (`model = resolve_arm_model(args)`) is NOT asserted here: an
    # AST check of the two call nodes stays green over a literal assignment to
    # `model` (measured), and a source grep is satisfied by this very docstring.
    # It is pinned where neither can fake it — the end-to-end drive in §7 asserts
    # that BOTH the artifact's `config.model` and the id handed to the timed call
    # are the resolver's value.


def test_the_read_side_vocabulary_is_the_one_rerank_pool_emits():
    """`READ_SIDE_DEGRADES` is a hand-copied vocabulary — pin it to its source.

    ⛔ It is a copy of string literals produced inside ``tortoise/rerank.py``, and
    the tests iterate the set itself, so both sides were the tool. A rename in
    ``rerank.py`` would make this instrument refuse EVERY query as "the rerank
    DEGRADED" (fail-closed, but a whole run lost) — and a REMOVED read-side reason
    would be worse: the run would refuse instead of counting the skip.
    """
    import inspect

    from tortoise import rerank as rr

    src = inspect.getsource(rr.rerank_pool)
    for reason in sorted(cal.READ_SIDE_DEGRADES):
        assert f'"{reason}"' in src, (
            f"{reason!r} is not a degrade_reason rerank_pool emits any more")


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
