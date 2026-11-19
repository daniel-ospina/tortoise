"""#8486 finding 3 — pins for `tools/longmem_eval/ingest_profile.py`.

The profiler is a DIAGNOSTIC, so its only correctness claims are about its
CALIBRATION: the synthetic haystack must be the real split's shape, or the
per-question projection it prints describes a workload nobody runs. These
tests are import-light (no lane, no DB, no embedder) and pin exactly that.

Run::

    uv run pytest tests/test_longmem_ingest_profile.py -q
"""
from __future__ import annotations

import json
import math
import os

import pytest

from tools.longmem_eval import ingest_profile as prof
from tools.longmem_eval.ingest import DEFAULT_CHUNK_TURNS, _session_chunks


def test_the_headline_shape_matches_the_measured_dataset_scale():
    """⛔ The calibration claim, pinned.

    `haystack_overlap.CENSUS` measured the 500-Q S split at 371,095 encode
    units = 742 points/question. The profiler's headline shape (25 sessions ×
    20 turns at ``chunk_turns=2``) must produce that many points, or the
    ``s/question`` projection it prints is about a different workload.
    """
    sessions, turns = prof.SCALING_SHAPES[-1]
    q = prof.synthetic_question("cal", sessions, turns,
                                prof.HEADLINE_TURN_CHARS)
    n_turns = sum(len(s) for s in q["haystack_sessions"])
    n_chunks = sum(len(list(_session_chunks(s, DEFAULT_CHUNK_TURNS)))
                   for s in q["haystack_sessions"])
    points = n_turns + n_chunks
    assert n_turns == 500
    assert points == 742 + 8  # 750 — within 1.1% of the measured 742
    assert abs(points - prof.DATASET_POINTS_PER_QUESTION) / \
        prof.DATASET_POINTS_PER_QUESTION < 0.02


def test_the_chunk_leg_is_one_per_chunk_turns_window():
    """The profile's cost is proportional to (turns + chunks); if the chunk
    leg silently disappeared (a `chunk_turns` default change), the projection
    would under-report by a third."""
    sessions, turns = 10, 20
    q = prof.synthetic_question("chunks", sessions, turns, 200)
    expected = sessions * math.ceil(turns / DEFAULT_CHUNK_TURNS)
    got = sum(len(list(_session_chunks(s, DEFAULT_CHUNK_TURNS)))
              for s in q["haystack_sessions"])
    assert got == expected == 100


def test_the_named_db_wins_over_an_ambient_uri(monkeypatch):
    """``--db`` must WIN over an exported ``TORTOISE_DB_URI`` — pinned.

    The round-2 review recorded this as a P3: ``_sdk`` was changed from
    ``os.environ.setdefault`` to an assignment so an ambient variable can no
    longer redirect the profiler's five throwaway graphs to a server the
    operator did not name, but nothing reddened if it were reverted.

    ``tools/test_lane.py`` exports exactly that variable on a smoke lane, so
    ``setdefault`` would silently measure a DIFFERENT server while
    ``report["db"]`` still said True. The SDK is stubbed: this pins the env
    contract, not a connection.

    Non-vacuity: with ``setdefault`` restored, the stub observes the ambient
    URI and the first assertion reds.
    """
    seen: dict[str, str | None] = {}

    class _StubSDK:
        def __init__(self, *_a, **_kw):
            seen["uri"] = os.environ.get("TORTOISE_DB_URI")

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@ambient:6379/other")

    _, cleanup = prof._sdk(db_uri="docker://:pw@named:6379/want",
                           namespace="profile-test")
    assert seen["uri"] == "docker://:pw@named:6379/want", (
        "an ambient TORTOISE_DB_URI must not outrank the --db it was given")
    cleanup()
    assert os.environ["TORTOISE_DB_URI"] == "docker://:pw@ambient:6379/other", (
        "cleanup must restore the ambient value it borrowed the variable from")


def test_the_named_db_also_borrows_the_runs_socket_bound(monkeypatch):
    """``--db`` must inherit the socket bound ``run_main`` gives a real run.

    Review round 6: the profiler set only ``TORTOISE_DB_URI``, so on a lane
    that exports that variable alone (``tools/test_lane.py``) it inherited the
    PRODUCT read bound instead of the eval's, and a single query past it raised
    ``redis TimeoutError`` and killed a rung — on the contended host this
    module exists to measure. The parity is the whole justification for the
    module, so it is pinned here rather than described.

    Non-vacuity: drop the two ``os.environ`` assignments in ``_sdk``'s ``--db``
    arm and the first two assertions red; stop restoring them in ``_cleanup``
    and the last one reds.
    """
    from tools.longmem_eval.stall_guard import (
        DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S,
        DEFAULT_EVAL_SOCKET_TIMEOUT_S,
        ENV_SOCKET_CONNECT_TIMEOUT,
        ENV_SOCKET_TIMEOUT,
    )

    class _StubSDK:
        def __init__(self, *_a, **_kw):
            pass

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.delenv(ENV_SOCKET_TIMEOUT, raising=False)
    monkeypatch.delenv(ENV_SOCKET_CONNECT_TIMEOUT, raising=False)

    _, cleanup = prof._sdk(db_uri="docker://:pw@named:6379/want",
                           namespace="profile-test")
    assert os.environ[ENV_SOCKET_TIMEOUT] == str(DEFAULT_EVAL_SOCKET_TIMEOUT_S)
    assert os.environ[ENV_SOCKET_CONNECT_TIMEOUT] == str(
        DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S)
    cleanup()
    assert ENV_SOCKET_TIMEOUT not in os.environ
    assert ENV_SOCKET_CONNECT_TIMEOUT not in os.environ


def test_the_ambient_uri_selects_the_server_arm_like_runpy(monkeypatch):
    """No ``--db`` + an exported ``TORTOISE_DB_URI`` must measure the SERVER.

    ``run_main`` promotes the ambient variable into the server path, so that is
    the path a real run takes on a lane that exports it — and choosing the
    embedded arm there would measure a different engine while the module's whole
    justification is parity with the run. Pinned because the docstrings claimed
    parity the code did not have (review round 7).
    """
    # ⛔ Discriminate on the CONSTRUCTOR SHAPE, not on the env var: the embedded
    # arm leaves TORTOISE_DB_URI untouched, so an env-only assertion passes on
    # both arms and pins nothing (measured — the first version of this test was
    # a false PASS against the un-promoted code). The server arm constructs
    # ``TortoiseSDK(namespace=…)``; the embedded arm passes a PATH positionally.
    seen: dict[str, object] = {}

    class _StubSDK:
        def __init__(self, *args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@ambient:6379/lane")

    _, cleanup = prof._sdk(db_uri=None, namespace="profile-test")
    assert seen["args"] == (), (
        "the SERVER arm takes no path — an embedded tempdir path here means the "
        "ambient URI was ignored and a different engine measured")
    assert seen["kwargs"] == {"namespace": "profile-test"}
    cleanup()
    assert os.environ["TORTOISE_DB_URI"] == "docker://:pw@ambient:6379/lane"


def test_with_no_uri_at_all_the_arm_is_embedded(monkeypatch):
    """The contrast half: no --db and no ambient URI => the embedded tempdir."""
    seen: dict[str, object] = {}

    class _StubSDK:
        def __init__(self, *args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

    _, cleanup = prof._sdk(db_uri=None, namespace="profile-test")
    args = seen["args"]
    assert len(args) == 1 and str(args[0]).endswith("lme.db"), (
        "an embedded tempdir path is the no-URI arm")
    assert seen["kwargs"] == {}
    cleanup()


def test_an_explicit_socket_bound_is_respected(monkeypatch):
    """A lane that DID set the bound keeps it — parity is not an override."""
    from tools.longmem_eval.stall_guard import ENV_SOCKET_TIMEOUT

    class _StubSDK:
        def __init__(self, *_a, **_kw):
            pass

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.setenv(ENV_SOCKET_TIMEOUT, "45")
    _, cleanup = prof._sdk(db_uri="docker://:pw@named:6379/want",
                           namespace="profile-test")
    assert os.environ[ENV_SOCKET_TIMEOUT] == "45"
    cleanup()
    assert os.environ[ENV_SOCKET_TIMEOUT] == "45"


#: A complete shape row, so ``main`` never needs a lane, a graph or an
#: embedder to finish: every field below is what the human render formats and
#: what the JSON report passes through unchanged.
_STUB_SHAPE = {
    "sessions": 1, "turns": 1, "chunks": 0, "points": 1,
    "fixed_setup_s": 0.0, "ingest_s": 0.0, "s_per_point": 0.0,
}


def _main_report(monkeypatch, capsys, argv: list[str]) -> dict:
    """Run ``main`` with the embedder and the measurement stubbed.

    Nothing here may touch a lane: ``EmbeddingModel.get`` is the first real
    dependency inside ``main`` (it loads the cached weights), and
    ``measure_shape`` writes a throwaway graph per rung. Both are replaced, so
    the report is the only thing under test.
    """
    class _StubModel:
        @staticmethod
        def get(*_a, **_kw):
            return None

    import tortoise.embeddings as embeddings

    monkeypatch.setattr(embeddings, "EmbeddingModel", _StubModel)
    monkeypatch.setattr(prof, "measure_shape", lambda **_kw: dict(_STUB_SHAPE))
    assert prof.main([*argv, "--json", "--no-profile"]) == 0
    return json.loads(capsys.readouterr().out)


def test_the_reported_arm_is_the_one_actually_selected(monkeypatch, capsys):
    """``report["db"]`` describes the ARM, not the ``--db`` flag.

    An exported ``TORTOISE_DB_URI`` with no ``--db`` takes the SERVER arm —
    ``_sdk`` promotes it exactly as ``run_main`` does (pinned by
    ``test_the_ambient_uri_selects_the_server_arm_like_runpy``) — so reporting
    ``bool(args.db)`` emitted ``"db": false`` about a server run: the artifact
    disagreeing with the run, which is #8486's own defect class, in the state
    the module calls the likeliest (``tools/test_lane.py`` exports this
    variable on every smoke lane).

    Non-vacuity: restore ``bool(args.db)`` and this row reds (``False`` against
    the assertion's ``True``) while the no-URI contrast below stays green.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@ambient:6379/lane")
    report = _main_report(monkeypatch, capsys, [])
    assert report["db"] is True, (
        "the ambient URI selects the SERVER arm, so the report must say so — "
        "otherwise the record describes a different engine than the run")


def test_the_reported_arm_is_embedded_with_no_uri_at_all(monkeypatch, capsys):
    """The contrast half: the report is not simply the constant ``True``."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    report = _main_report(monkeypatch, capsys, [])
    assert report["db"] is False, (
        "no --db and no ambient URI is the embedded arm, and the report must "
        "name it rather than defaulting to the server")


def test_an_empty_db_is_refused_instead_of_silently_embedded(monkeypatch,
                                                             capsys):
    """``--db ""`` must ERROR, not measure a different engine.

    ``--db "$TORTOISE_DB_URI"`` with the variable unset is the ordinary shape
    of this mistake. ``"" is not None`` promotes nothing and ``""`` is falsy,
    so without the guard the profiler silently took the EMBEDDED arm — five
    throwaway graphs on a different engine, with no error — while ``run_main``
    refuses the same argument. This pins the refusal (and its parity with
    ``run_main``'s message) at ``main``'s own entry point.

    Non-vacuity: delete the ``"://" not in args.db`` guard and the stubbed
    measurement below is REACHED, so the ``_never`` raise replaces the
    ``SystemExit`` and this row reds.
    """
    def _never(**_kw):
        raise AssertionError(
            "--db '' must be refused before any rung is measured")

    class _NeverModel:
        @staticmethod
        def get(*_a, **_kw):
            raise AssertionError(
                "--db '' must be refused before the embedder is loaded")

    import tortoise.embeddings as embeddings

    monkeypatch.setattr(embeddings, "EmbeddingModel", _NeverModel)
    monkeypatch.setattr(prof, "measure_shape", _never)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        prof.main(["--db", "", "--json", "--no-profile"])
    assert exit_info.value.code == 2, "an argparse error exits 2, not 0"
    err = capsys.readouterr().err
    assert "--db must be a FalkorDB URI" in err, (
        "the refusal must be ``run_main``'s own message, so the two entry "
        "points cannot drift into different answers for the same argument")
    assert "got ''" in err, "the message must name the value it refused"


def test_the_evidence_turn_is_marked_once_per_question():
    """Turn/evidence denominators the ingest reports must be derivable from
    the fixture, so a shape change cannot quietly alter what it measures."""
    q = prof.synthetic_question("evi", 3, 4, 200)
    marked = sum(1 for s in q["haystack_sessions"]
                 for t in s if t["has_answer"])
    assert marked == 1


def test_every_documented_turn_length_is_built():
    """The two measured turn lengths (900 = the real ~230 tokens/turn, 200 =
    the short shape that makes the embed term's length sensitivity visible)
    must actually differ in what they build."""
    short = prof.synthetic_question("s", 1, 2, prof.SHORT_TURN_CHARS)
    long = prof.synthetic_question("l", 1, 2, prof.HEADLINE_TURN_CHARS)
    assert len(short["haystack_sessions"][0][0]["content"]) == \
        prof.SHORT_TURN_CHARS
    assert len(long["haystack_sessions"][0][0]["content"]) == \
        prof.HEADLINE_TURN_CHARS


def test_the_embedded_arm_raises_the_redislite_start_budget(monkeypatch):
    """The no-``--db`` arm carries the #1944 hardening, not just the parity claim.

    ``run.py``'s ``_make_question_sdk`` raises the vendored redislite start
    budget to 60 s because the 10 s default RACES on a contended box (the
    falkordb.so module load + socket bind can exceed it) and
    ``embedded_lifecycle`` reads that budget from the same class attribute.
    The profiler exists to be run on exactly that box, so an embedded profile
    that keeps the 10 s default dies on the FIRST rung with a
    ``RedisLiteServerStartError`` that says nothing about ingest cost.

    Non-vacuity: delete the ``if _rc.Redis.start_timeout < 60`` block from
    ``_sdk``'s embedded arm and this test reds (the stub SDK means nothing else
    can raise the value); set the budget above 60 and it stays at that value,
    which is why the guard is a floor rather than an assignment.
    """
    import redislite.client as rc

    class _StubSDK:
        def __init__(self, *_a, **_kw):
            pass

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.setattr(rc.Redis, "start_timeout", 10, raising=False)
    # ⛔ The embedded arm is reached only with NO uri AT ALL: an exported
    # TORTOISE_DB_URI takes the server path, exactly as ``run_main`` does (and
    # the smoke lane exports it — ``tools/test_lane.py``). Without this the
    # test silently exercises the server arm in CI and reds on the floor assert.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

    _, cleanup = prof._sdk(db_uri=None, namespace="profile-test")
    assert rc.Redis.start_timeout == 60, (
        "the embedded arm must carry run.py's #1944 start-budget floor")
    cleanup()

    # a budget already above the floor is left alone (floor, not override)
    monkeypatch.setattr(rc.Redis, "start_timeout", 120, raising=False)
    _, cleanup = prof._sdk(db_uri=None, namespace="profile-test")
    assert rc.Redis.start_timeout == 120
    cleanup()

# ── the promotion must validate what it promotes (review round 9) ──────────
# `main()`'s arm reporting and its `--db ""` refusal are pinned by
# `test_the_reported_arm_is_the_one_actually_selected`,
# `test_the_reported_arm_is_embedded_with_no_uri_at_all` and
# `test_an_empty_db_is_refused_instead_of_silently_embedded` (a peer commit on
# this branch landed equivalents while this round was in flight — the three
# duplicates written here were dropped rather than kept as parallel pins that
# would drift). What is NOT pinned there is the ambient-URI validation below.
def _stub_measurements(monkeypatch):
    """Stub everything ``main()`` would spend minutes on."""
    import tortoise.embeddings as emb
    from tools.longmem_eval import ingest_profile as prof_mod

    calls: dict[str, object] = {}

    class _NoModel:
        @staticmethod
        def get(**_kw):
            return None

    def _fake_measure_shape(**kw):
        calls.setdefault("shapes", []).append(kw.get("db_uri"))
        return {"db_uri": kw.get("db_uri")}

    monkeypatch.setattr(emb, "EmbeddingModel", _NoModel)
    monkeypatch.setattr(prof_mod, "measure_shape", _fake_measure_shape)
    return calls


def test_sdk_refuses_an_ambient_uri_that_is_not_a_uri(monkeypatch):
    """The promotion must validate what it promotes, like ``run_main``.

    ``run_main`` runs the URI-shape check on the PROMOTED value; promoting
    without it hands ``TortoiseSDK`` garbage and the error names the SDK, not
    the mistake.
    """
    import pytest as _pytest

    class _StubSDK:
        def __init__(self, *_a, **_kw):
            raise AssertionError("must not construct on an invalid URI")

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.setenv("TORTOISE_DB_URI", "notauri")
    with _pytest.raises(ValueError, match="must be a FalkorDB URI"):
        prof._sdk(db_uri=None, namespace="profile-test")
