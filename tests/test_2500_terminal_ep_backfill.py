"""#2500 — the pre-#2490 terminal-EP backfill is SELECTIVE and idempotent.

#2490 fixed terminalization going forward: every terminalizing WRITE decays a
claim's stored belief to vacuity (``live.decay_clause`` → confidence 0.5,
posterior (1,1)). A claim that was ALREADY terminal when that shipped keeps the
frozen pre-terminal posterior it was last measured with, and two include-terminal
surfaces read that stored posterior directly: ``GraphRanker._fetch_signals``
(the ``order_by="confidence"`` sort key — no terminal gate) and
``annotate_ep_batch``'s ``confidence_mean``.

``graph-scripts/2500_backfill_terminal_ep_vacuity.py`` aligns those rows. These
tests are deliberately SELECTIVE — a sweep that decays everything, drops the
prior history, or writes on a dry run must FAIL here:

  1. a LIVE claim with a healthy posterior is NOT touched;
  2. a TERMINAL claim with a frozen posterior IS decayed to (0.5, 1.0, 1.0);
  3. ``ep_alpha``/``ep_beta`` survive (the #2490 recovery vector);
  4. the ``outdated=true``-only shape (``invalidate_point``: flag set, status
     untouched) IS swept;
  5. a prior-only terminal claim (no posterior, no confidence) is OUT of scope;
  6. a second run reports ``found == 0`` (idempotent by construction);
  7. ``--dry-run`` writes NOTHING and reports the same ``found``;
  8. the issue's LITERAL post-condition is pinned as unsatisfiable, and the
     corrected one (the sweep predicate) as the real post-condition.
"""
from __future__ import annotations

import importlib.util as _ilu
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "graph-scripts" / "2500_backfill_terminal_ep_vacuity.py"
_spec = _ilu.spec_from_file_location("backfill_terminal_ep_vacuity_2500",
                                     str(_SCRIPT_PATH))
sweep = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

# graph-scripts/ is a hyphenated dir; the module name starts with a digit, so
# it cannot be reached with an `import` statement — the spec loader above is
# the same pattern as tests/test_4220_blog_residue_cleanup.py.

from tortoise.live import TERMINAL_EXCLUDED_STATUSES  # noqa: E402
from tortoise.ranking import GraphRanker  # noqa: E402
from tortoise.search_engine import annotate_ep_batch  # noqa: E402


@pytest.fixture
def sdk(sdk_factory):
    s = sdk_factory()
    yield s
    s.close()


# ── fixtures: seed PRE-#2490 rows by direct write ─────────────────────────
#
# The frozen shape is a row that predates #2490's write-time decay, so it
# cannot be produced by the terminalizing write path AND is exactly why the
# sweep exists. We therefore plant the stored columns directly — the same
# technique tests/test_ep_terminal_ghost.py::plant_highvar_terminal uses.

_FROZEN = {"confidence": 0.9, "posterior_alpha": 9.0, "posterior_beta": 1.0,
           "ep_alpha": 7.0, "ep_beta": 3.0}


def _terminal_count(graph) -> int:
    """The issue's LITERAL post-condition predicate."""
    return int(graph.query(
        f"MATCH (n:Point) WHERE {sweep._terminal_expression('n.status')} "
        "AND (n.posterior_alpha IS NOT NULL OR n.confidence IS NOT NULL) "
        "RETURN count(n)"
    ).result_set[0][0] or 0)


def _sweep_predicate_count(graph) -> int:
    """The sweep's own match-set size — the REAL post-condition."""
    return int(graph.query(
        f"MATCH (n:Point) WHERE {sweep._sweep_where('n')} RETURN count(n)"
    ).result_set[0][0] or 0)


def _freeze(sdk, content: str, *, status: str, outdated: bool = False,
            **overrides) -> str:
    """Create a Point and plant the PRE-#2490 stored state on it directly."""
    pid = sdk.create_point("statement", content, status="live")["id"]
    cols = {**_FROZEN, **overrides}
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=$status, n.outdated=$outdated, "
        "n.confidence=$confidence, n.posterior_alpha=$posterior_alpha, "
        "n.posterior_beta=$posterior_beta, n.ep_alpha=$ep_alpha, n.ep_beta=$ep_beta",
        params={"id": pid, "status": status, "outdated": outdated, **cols},
    )
    return pid


def _stored(sdk, pid: str) -> dict:
    r = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, coalesce(n.outdated, false), "
        "n.confidence, n.posterior_alpha, n.posterior_beta, n.ep_alpha, n.ep_beta",
        params={"id": pid},
    ).result_set[0]
    return {"status": r[0], "outdated": bool(r[1]), "confidence": r[2],
            "posterior_alpha": r[3], "posterior_beta": r[4],
            "ep_alpha": r[5], "ep_beta": r[6]}


# ── 1. selectivity: a LIVE healthy claim is never touched ─────────────────

def test_live_claim_with_healthy_posterior_is_untouched(sdk):
    live = _freeze(sdk, "live healthy claim", status="live")
    report = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert report["found"] == 0, "the sweep must not consider a live claim"
    assert report["swept"] == 0
    assert _stored(sdk, live) == {
        "status": "live", "outdated": False, "confidence": 0.9,
        "posterior_alpha": 9.0, "posterior_beta": 1.0,
        "ep_alpha": 7.0, "ep_beta": 3.0,
    }, "a live claim's posterior must survive the sweep byte-for-byte"


def test_live_claim_is_untouched_even_alongside_a_terminal_one(sdk):
    """A discriminating run: one terminal row swept, the live row beside it not."""
    live = _freeze(sdk, "live bystander", status="live")
    dead = _freeze(sdk, "retracted neighbour", status="retracted")
    report = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert report["found"] == 1 and report["swept"] == 1
    assert _stored(sdk, live)["confidence"] == 0.9
    assert _stored(sdk, live)["posterior_alpha"] == 9.0
    assert _stored(sdk, dead)["confidence"] == 0.5


# ── 2/3. the terminal row is decayed, the prior history is preserved ──────

@pytest.mark.parametrize("status", sorted(TERMINAL_EXCLUDED_STATUSES))
def test_terminal_status_with_frozen_posterior_is_decayed(sdk, status):
    """Every status in the #2901 terminal vocabulary is swept (``deprecated``
    included — a legacy-write-only status, planted by direct write)."""
    pid = _freeze(sdk, f"frozen {status} claim", status=status)
    report = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert report["found"] == 1 and report["swept"] == 1
    # The report NAMES the pre-write stored values (the operator's pre-flight).
    (rid, rstatus, routdated, rconf, rpa, rpb), = report["points"]
    assert (rid, rstatus, routdated) == (pid, status, False)
    assert (rconf, rpa, rpb) == (0.9, 9.0, 1.0)
    after = _stored(sdk, pid)
    assert (after["confidence"], after["posterior_alpha"],
            after["posterior_beta"]) == (0.5, 1.0, 1.0)
    # ep_alpha/ep_beta are the persisted prior history and the #2490 recovery
    # vector — the sweep must NOT touch them.
    assert (after["ep_alpha"], after["ep_beta"]) == (7.0, 3.0)
    # The lifecycle columns are untouched too.
    assert (after["status"], after["outdated"]) == (status, False)


def test_terminal_decay_is_visible_to_the_readers_named_in_the_issue(sdk):
    """The issue's Indicator, localised: the frozen terminal claim ranks at
    full belief on ``order_by="confidence"``/``annotate_ep_batch`` BEFORE the
    sweep and at vacuity after; the live claim is unchanged throughout."""
    dead = _freeze(sdk, "terminal zzqfrozen claim", status="retracted")
    live = _freeze(sdk, "live zzqfrozen claim", status="live")
    proj = sdk._get_proj()
    g = proj.g

    before = GraphRanker(proj)._fetch_signals([dead, live], "point")
    assert before[dead]["confidence"] == pytest.approx(0.9)
    assert annotate_ep_batch(g, [dead])[dead].confidence_mean == pytest.approx(0.9)

    report = sweep.backfill_terminal_ep_vacuity(proj)
    assert report["swept"] == 1

    after = GraphRanker(proj)._fetch_signals([dead, live], "point")
    assert after[dead]["confidence"] == pytest.approx(0.5), (
        "the frozen terminal claim must no longer rank at full belief")
    assert after[live]["confidence"] == pytest.approx(0.9)
    assert annotate_ep_batch(g, [dead])[dead].confidence_mean == pytest.approx(0.5)


# ── 4. the invalidate_point shape: flag set, status untouched ─────────────

def test_outdated_flag_without_terminal_status_is_swept(sdk):
    pid = _freeze(sdk, "flagged but status live", status="live", outdated=True)
    report = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert report["found"] == 1 and report["swept"] == 1
    after = _stored(sdk, pid)
    assert after["status"] == "live", "the sweep must not rewrite status"
    assert after["outdated"] is True
    assert (after["confidence"], after["posterior_alpha"],
            after["posterior_beta"]) == (0.5, 1.0, 1.0)


# ── 5. scope: a prior-only terminal claim is OUT of the sweep ─────────────

def test_prior_only_terminal_claim_is_out_of_scope(sdk):
    """No stored posterior and no confidence → nothing frozen to vacate. The
    prior (ep_alpha/ep_beta) is the history #2490 preserves; writing a
    posterior onto it would override the prior read, not vacate a measurement.

    The second row is the DEGENERATE shape that makes the sweep's MEASURED
    clause (``posterior_alpha IS NOT NULL OR confidence IS NOT NULL``)
    load-bearing rather than merely descriptive: a stray ``posterior_beta``
    with no alpha and no confidence is NOT a writer-produced posterior state
    (EP always flushes alpha+beta+confidence together), so it is out of scope
    by the issue's own predicate.
    """
    pid = sdk.create_point("statement", "prior-only retracted",
                           status="live")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='retracted', n.ep_alpha=7.0, "
        "n.ep_beta=3.0", params={"id": pid})
    degenerate = sdk.create_point("statement", "beta-only retracted",
                                  status="live")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='retracted', "
        "n.posterior_beta=3.0", params={"id": degenerate})
    report = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert report["found"] == 0 and report["swept"] == 0
    after = _stored(sdk, pid)
    assert after["confidence"] is None
    assert after["posterior_alpha"] is None
    assert (after["ep_alpha"], after["ep_beta"]) == (7.0, 3.0)
    assert _stored(sdk, degenerate)["posterior_beta"] == 3.0


def test_partial_decay_row_is_completed(sdk):
    """A row mid-decay (alpha and confidence already vacuous, beta not) is what
    the clause-2-and-beta-arm combination exists for: ``posterior_alpha IS NOT
    NULL`` keeps it in scope, and the beta arm is what marks it non-vacuous."""
    pid = _freeze(sdk, "partial decay", status="retracted", confidence=0.5,
                  posterior_alpha=1.0, posterior_beta=3.0)
    report = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert report["found"] == 1 and report["swept"] == 1
    after = _stored(sdk, pid)
    assert (after["confidence"], after["posterior_alpha"],
            after["posterior_beta"]) == (0.5, 1.0, 1.0)


# ── 6. idempotency ────────────────────────────────────────────────────────

def test_second_run_reports_found_zero(sdk):
    _freeze(sdk, "retracted one", status="retracted")
    _freeze(sdk, "flagged one", status="live", outdated=True)
    first = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert first["found"] == 2 and first["swept"] == 2
    second = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert second == {"found": 0, "swept": 0, "points": []}


# ── 7. dry-run writes nothing but reports the same match set ──────────────

def test_dry_run_writes_nothing_and_reports_the_match_set(sdk):
    pid = _freeze(sdk, "dry-run target", status="superseded")
    dry = sweep.backfill_terminal_ep_vacuity(sdk._get_proj(), dry_run=True)
    assert dry["found"] == 1 and dry["swept"] == 0
    assert dry["points"][0][0] == pid
    frozen = _stored(sdk, pid)
    assert (frozen["confidence"], frozen["posterior_alpha"]) == (0.9, 9.0), (
        "a dry run must not write anything")
    real = sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    assert real["found"] == 1 and real["swept"] == 1


# ── 8. the post-condition: the issue's LITERAL one is unsatisfiable ───────

def test_issue_literal_postcondition_is_unsatisfiable_but_sweep_predicate_is_zero(sdk):
    """The issue's Indicator asks for 0 rows matching ``terminal AND
    (posterior_alpha IS NOT NULL OR confidence IS NOT NULL)`` after the run.

    That can NEVER hold: the write SETS those columns to 1.0/0.5, so the
    literals stay NOT NULL by design. The satisfiable post-condition is the
    sweep's own predicate (no terminal row whose stored value DIFFERS from the
    vacuous tuple) — pinned here as the real contract, with the literal one
    pinned as permanently non-zero so a future reader does not "fix" the script
    to chase an impossible report.
    """
    for i, status in enumerate(sorted(TERMINAL_EXCLUDED_STATUSES)):
        _freeze(sdk, f"frozen {status} {i}", status=status)
    sweep.backfill_terminal_ep_vacuity(sdk._get_proj())
    g = sdk._get_proj().g
    assert _sweep_predicate_count(g) == 0, "the real post-condition must hold"
    assert _terminal_count(g) == len(TERMINAL_EXCLUDED_STATUSES), (
        "the issue's literal predicate stays non-zero — the write keeps the "
        "columns NOT NULL, which is the corrected reading of its Indicator")


# ── the sweep's write is #2490's decay, verbatim ──────────────────────────

def test_sweep_reuses_the_canonical_decay_clause():
    """The idempotency target is derived from ``live.decay_clause`` itself, so
    the backfill cannot drift from the write-time fix it mirrors."""
    assert sweep._VACUOUS == {"confidence": 0.5, "posterior_alpha": 1.0,
                              "posterior_beta": 1.0}
    _, _, sweep_stmt = sweep._statements()
    assert "n.confidence=0.5" in sweep_stmt
    assert "n.posterior_alpha=1.0" in sweep_stmt
    assert "n.posterior_beta=1.0" in sweep_stmt


# ── the guard gates on the SDK-RESOLVED graph name, not the URI path ──────

def _install_fake_sdk(monkeypatch, resolved_name: str) -> dict:
    calls: dict = {}

    class _FakeSDK:
        def __init__(self, *args, **kwargs):
            calls["sdk_kwargs"] = kwargs

        def _get_proj(self):
            return SimpleNamespace(g=SimpleNamespace(name=resolved_name))

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    return calls


def _run_main(monkeypatch, argv: list[str]) -> tuple[str, SystemExit | None]:
    # ``main()`` sets ``os.environ["TORTOISE_DB_URI"]`` before constructing the
    # SDK, so the env var must be restored at teardown — otherwise this test
    # leaks a live-server URI into every later test module in the process (the
    # ``sdk_factory`` lane flip, which is how this file's own run once broke
    # test_terminal_status_vocabulary's graph-backed section).
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise")
    monkeypatch.setattr(sys, "argv", ["2500_backfill_terminal_ep_vacuity", *argv])
    out = io.StringIO()
    exc: SystemExit | None = None
    recorded: dict = {}

    def _fake_sweep(_proj, *, dry_run=False):
        recorded["dry_run"] = dry_run
        return {"found": 0, "swept": 0, "points": []}

    monkeypatch.setattr(sweep, "backfill_terminal_ep_vacuity", _fake_sweep)
    try:
        with redirect_stdout(out):
            sweep.main()
    except SystemExit as e:
        exc = e
    return out.getvalue(), exc


def test_guard_refuses_a_non_test_resolved_name_without_yes():
    with pytest.raises(SystemExit):
        sweep.test_guard("tortoise", yes=False)
    sweep.test_guard("tortoise", yes=True)  # explicit consent


def test_guard_auto_approves_a_test_prefixed_resolved_name():
    sweep.test_guard("test_abcd1234_tortoise", yes=False)
    sweep.test_guard("tortoise_test_matrix", yes=False)


def test_main_gates_on_the_sdk_resolved_graph_not_the_uri_path(monkeypatch):
    """A test-prefixed ``--uri`` path whose SDK-resolved graph is production
    must still REFUSE — the #5188/#4292 bypass shape, and the reason
    ``test_guard`` takes the resolved name."""
    _install_fake_sdk(monkeypatch, "tortoise")
    out, exc = _run_main(monkeypatch, ["--uri", "docker://:x@localhost:6379/test_foo"])
    assert isinstance(exc, SystemExit) and exc.code == 1
    assert "NOT a test graph" in out


def test_main_proceeds_on_a_test_resolved_graph_and_honours_dry_run(monkeypatch):
    calls = _install_fake_sdk(monkeypatch, "test_abcd1234_tortoise")
    out, exc = _run_main(monkeypatch, ["--dry-run"])
    assert exc is None
    assert "Test graph detected" in out
    assert "[DRY-RUN]" in out, "the --dry-run mode banner must be reported"
    assert calls["closed"] is True
