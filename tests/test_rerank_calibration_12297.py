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
