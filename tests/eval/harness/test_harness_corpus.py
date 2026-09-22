"""W3 harness corpus integrity tests (issue #2099 W3-a).

Hermetic: committed corpus = fixtures/ + gold/ + _manifest.json must be a
byte-identical deterministic render; the fixtures_hash is stable; the
manifest verifies; holdout membership is PINNED per fixture (never
seed-derived); suite coverage satisfies the corpus floor; no answer-key
contamination (gold never inside a fixture).
"""
from __future__ import annotations

from eval.harness import corpus, schema
from eval.harness import generate_corpus as gen


def test_corpus_render_is_byte_identical():
    """The committed corpus is a fresh deterministic render (CI drift gate)."""
    assert gen.check_drift() == []


def test_corpus_validates_clean():
    assert gen.validate_committed() == []


def test_fixtures_hash_covers_gold_changes():
    """A gold-only edit changes fixtures_hash (answer-key edits must
    invalidate committed baselines — never a silent compare)."""
    hash_before = corpus.compute_fixtures_hash()
    gold_path = corpus.gold_path(corpus.session_ids()[0])
    original = gold_path.read_bytes()
    try:
        gold_path.write_bytes(original + b"\n")  # whitespace-only change
        assert corpus.compute_fixtures_hash() != hash_before
    finally:
        gold_path.write_bytes(original)
    assert corpus.compute_fixtures_hash() == hash_before


def test_manifest_verifies_and_detects_drift():
    result = corpus.verify_manifest()
    assert result["ok"] is True
    assert result["fixtures_hash"] == corpus.compute_fixtures_hash()


def test_no_gold_inside_fixtures():
    for sid in corpus.session_ids():
        fixture = corpus.load_fixture(sid)
        assert "gold" not in fixture, f"{sid}: answer-key inside fixture"


def test_holdout_pinned_per_fixture():
    """Holdout membership is an explicit per-fixture flag — a seed re-roll
    must never move a session between train/holdout."""
    for sid in corpus.session_ids():
        fixture = corpus.load_fixture(sid)
        assert isinstance(fixture.get("holdout"), bool)
    holdout = corpus.holdout_ids()
    assert holdout, "corpus must have pinned holdout members"
    n_holdout = len(holdout)
    n_total = len(corpus.session_ids())
    ratio = n_holdout / n_total
    assert ratio >= 0.05, f"holdout {n_holdout}/{n_total} below floor"
    # Stability: holdout derives from the flag only.
    assert holdout == sorted(
        sid for sid in corpus.session_ids()
        if corpus.load_fixture(sid).get("holdout") is True
    )


def test_suite_coverage_floor():
    """All five suites present; the graded-today surface has multiple
    sessions (write_back / continuity / isolation are graded TODAY — no
    reflex dependency)."""
    suites = {corpus.load_fixture(sid)["suite"] for sid in corpus.session_ids()}
    assert suites == schema.SUITE_VALUES
    per_suite = {}
    for sid in corpus.session_ids():
        suite = corpus.load_fixture(sid)["suite"]
        per_suite[suite] = per_suite.get(suite, 0) + 1
    for suite in ("write_back", "continuity", "isolation"):
        assert per_suite[suite] >= 2, f"{suite}: needs ≥ 2 graded-today sessions"
    assert per_suite["know_to_ask"] >= 2
    assert per_suite["push"] >= 2


def test_continuity_pairs_are_complete():
    """Every continuity writer has its reader; every reader names a writer
    that exists (the replay orders writers before readers)."""
    sids = set(corpus.session_ids())
    for sid in corpus.session_ids():
        fixture = corpus.load_fixture(sid)
        if fixture.get("suite") != "continuity":
            continue
        gold = corpus.load_gold(sid)
        spec = (gold.get("continuity") or {})
        writer = spec.get("writer_session")
        if fixture.get("writer"):
            reader = sid.replace("writer", "reader")
            assert reader in sids, f"{sid}: missing reader {reader}"
        else:
            assert writer in sids, f"{sid}: unknown writer {writer}"


def test_isolation_teams_disjoint_and_overlapping():
    """Isolation fixtures: both teams present with an overlapping entity
    name (Mercury/Atlas/Orion) but disjoint authored facts — the leak test's
    premise."""
    teams = {}
    for sid in corpus.session_ids():
        fixture = corpus.load_fixture(sid)
        if fixture.get("team"):
            teams.setdefault(fixture["team"], []).append(sid)
    assert set(teams) == {"team_a", "team_b"}
    # Overlapping entity names across teams exist (authored 'Mercury' +
    # shared write_back/continuity fixtures with team tags).
    a_names = {sid.split("_")[1] for sid in teams["team_a"]}
    b_names = {sid.split("_")[1] for sid in teams["team_b"]}
    assert a_names & b_names, "teams must share entity names to test leaks"


def test_both_posture_baselines_pending_until_published():
    """Both posture baselines exist and validate; published state is a
    deliberate act (the runner blesses at a clean committed head)."""
    for posture in ("llm", "m2"):
        baseline = corpus.load_baseline(posture=posture)
        assert schema.validate_baseline(baseline) == []
        assert baseline["fixtures_hash"] == corpus.compute_fixtures_hash()
