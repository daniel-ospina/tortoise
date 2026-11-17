"""#13084 part 2 — the demand census: query mix and seed hit rate, pinned.

The two figures added to the read ledger's report. The other two the issue asks
for already have instruments: the touched-node share is `hot_fraction` (`f`) and
the cold-restore cost is measured by the probe in `tools/ram_window_census.py`.

The properties that matter, and why:

* the mix counts **calls**, including ones that returned nothing (a mix of hits
  is a different, flattering question);
* an unrecognised kind **raises** rather than minting a fifth bucket;
* an older ledger's unlabelled calls are **counted as unattributed**, never
  folded into a bucket;
* the seed hit rate counts a seed as HOT only when that exact `(label, key)` had
  an earlier read **in the window** — stock membership would report ~100 % by
  construction, since every projected row is stock;
* both are **per tenant**: a union of two tenants answers a question nobody asked.
"""
from __future__ import annotations

import json

import pytest

from tortoise import read_ledger


def _write(path, *lines):
    path.write_text("".join(json.dumps(line) + "\n" for line in lines),
                    encoding="utf-8")
    return path


def _stock(key, *, at="2026-11-16T10:00:00+00:00", label="Point", tenant="t1"):
    return {"event": "stock", "label": label, "key": key, "bytes": 100,
            "at": at, "tenant": tenant}


def _read(key, *, at="2026-11-16T10:01:00+00:00", label="Point", tenant="t1"):
    return {"event": "read", "label": label, "key": key, "at": at,
            "tenant": tenant}


def _query(kind, *, seed=None, at="2026-11-16T10:02:00+00:00", label="Point",
           returned=3, tenant="t1"):
    return {"event": "query", "kind": kind, "label": label, "seed": seed,
            "returned": returned, "at": at, "tenant": tenant}


def test_record_query_refuses_a_kind_outside_the_closed_set(tmp_path):
    with pytest.raises(ValueError, match="closed set"):
        read_ledger.record_query("fuzzy", path=tmp_path / "l.jsonl")


def test_the_mix_counts_CALLS_including_empty_ones(tmp_path):
    p = tmp_path / "l.jsonl"
    for kind in ("vector", "vector", "text", "lookup", "traversal"):
        read_ledger.record_query(kind, returned=0, path=p)
    mix = read_ledger.query_mix(read_ledger.fold(p))
    assert mix.total == 5
    assert mix.counts == {"traversal": 1, "vector": 2, "text": 1, "lookup": 1}
    assert mix.shares["vector"] == pytest.approx(0.4)
    assert mix.unattributed == 0


def test_an_unlabelled_query_is_COUNTED_not_folded_into_a_bucket(tmp_path):
    """A pre-#13084 line (or an undated one) must not read as, say, `lookup`."""
    p = _write(tmp_path / "l.jsonl",
               {"event": "query", "label": "Point", "returned": 1,
                "at": "2026-11-16T10:00:00+00:00", "tenant": "t1"},
               _query("text"))
    mix = read_ledger.query_mix(read_ledger.fold(p))
    assert mix.counts["text"] == 1
    assert mix.total == 1
    assert mix.unattributed == 1


def test_seed_hit_rate_counts_a_seed_hot_only_after_an_earlier_read(tmp_path):
    p = _write(
        tmp_path / "l.jsonl",
        _stock("p-hot"),
        _stock("p-cold"),
        _stock("p-never-read"),
        _read("p-hot", at="2026-11-16T10:01:00+00:00"),
        # the seed was read before the traversal → HOT
        _query("traversal", seed="p-hot", at="2026-11-16T10:02:00+00:00"),
        # registered but never read → COLD (a miss)
        _query("traversal", seed="p-cold", at="2026-11-16T10:03:00+00:00"),
        # no seed key at all → a HOLE, not a miss
        _query("traversal", seed=None, at="2026-11-16T10:04:00+00:00"),
        # a seed the ledger never saw as stock → a second kind of hole
        _query("traversal", seed="p-ghost", at="2026-11-16T10:05:00+00:00"),
    )
    seeds = read_ledger.seed_hit_rate(read_ledger.fold(p))
    assert (seeds.trials, seeds.hot) == (2, 1)
    assert seeds.rate == pytest.approx(0.5)
    assert seeds.unseeded == 1
    assert seeds.unstocked == 1


def test_a_read_AFTER_the_traversal_is_not_residency_at_traversal_time(tmp_path):
    """Order matters: the question is whether the seed was already resident."""
    p = _write(
        tmp_path / "l.jsonl",
        _stock("p"),
        _query("traversal", seed="p", at="2026-11-16T10:01:00+00:00"),
        _read("p", at="2026-11-16T10:02:00+00:00"),
    )
    seeds = read_ledger.seed_hit_rate(read_ledger.fold(p))
    assert (seeds.trials, seeds.hot) == (1, 0)


def test_the_mix_and_the_seed_rate_are_PER_TENANT(tmp_path):
    p = _write(
        tmp_path / "l.jsonl",
        _stock("p", tenant="t1"),
        _read("p", tenant="t1"),
        _query("traversal", seed="p", tenant="t1"),
        _query("vector", tenant="t2"),
    )
    one = read_ledger.fold(p, tenant="t1")
    two = read_ledger.fold(p, tenant="t2")
    assert read_ledger.query_mix(one).counts["traversal"] == 1
    assert read_ledger.query_mix(one).counts["vector"] == 0
    assert read_ledger.query_mix(two).counts["vector"] == 1
    assert read_ledger.seed_hit_rate(one).rate == pytest.approx(1.0)
    assert read_ledger.seed_hit_rate(two).rate is None


def test_the_cli_reports_both_beside_f(tmp_path, capsys):
    p = _write(
        tmp_path / "l.jsonl",
        _stock("p"),
        _read("p"),
        _query("traversal", seed="p"),
        _query("vector"),
    )
    assert read_ledger._main(["--ledger", str(p), "--tenant", "t1",
                              "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["query_mix"]["counts"]["traversal"] == 1
    assert out["query_mix"]["counts"]["vector"] == 1
    assert out["seed_hit_rate"]["rate"] == pytest.approx(1.0)
    assert out["f_keys"] == pytest.approx(1.0)
