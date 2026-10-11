"""#5971 — the e2e-live DETECTOR and its REMEDIATOR must agree on scope.

The defect these tests pin is a scope mismatch, not a missing delete:

* the detector (``graph-scripts/e2e_live_reconcile.py``) counts every row in the
  namespace — ``email LIKE 'e2e-live-%@premise-labs.dev'``, ALL-TIME;
* the remediator (``graph-scripts/2146_e2e_live_orphan_cleanup.py``) enumerated
  for deletion only Guard A's strict shape ``^e2e-live-[0-9a-f]{8}@premise-labs\\.dev$``
  **and** the historical red window (2026-08-19 → 2026-09-03).

So a row inside the namespace but outside either remediator bound was DETECTED
forever and deleted by NO phase by default — and silently, so the weekly
auto-filed issue read as "just not cleaned yet" while nothing in the repo could
clean it. The live instance is the #3781 manual-verification account
``e2e-live-3781-<hex>@premise-labs.dev`` (PR #3790's commit message says the
operator had no ``SUPABASE_SERVICE_KEY``; the row sat in prod from 2026-09-17 and
kept issue #4445 / #5971 red).

The fix has two halves, both pinned here:

1. **parity by declaration** — both scripts keep their own regex, and this module
   asserts they are the SAME pattern and the SAME namespace literal, so widening
   one without the other reds a test instead of silently re-opening the gap;
2. **residue is never silent** — the remediator always enumerates the
   off-guard-shape rows on ``--phase enumerate|all``, prints them on every phase
   and records them in the manifest, and promotes them into
   the delete set only behind the explicit, review-gated ``--include-residue``
   (the ``--all-e2e-live`` shape: never a widened regex); the detector reports
   the same set as its own ``residue`` key with FULL emails.

DB-free and offline: every test drives a FAKE ``run_sql``/runner.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GS = _REPO_ROOT / "graph-scripts"


def _load(name: str, filename: str):
    """Load a ``graph-scripts/`` module by path.

    The directory is hyphenated and both filenames start with a digit, so they
    are unreachable with an ``import`` statement — the same spec-loader pattern
    as ``tests/test_2500_terminal_ep_backfill.py``.
    """
    spec = _ilu.spec_from_file_location(name, str(_GS / filename))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


detector = _load("e2e_live_reconcile_detector", "e2e_live_reconcile.py")
remediator = _load("e2e_live_orphan_cleanup_2146", "2146_e2e_live_orphan_cleanup.py")

# The namespace literal both scripts scope on (the detector's COUNT scope is the
# remediator's Guard-D scope). Declared once here so a change to one script's
# spelling cannot pass unnoticed.
NAMESPACE_LIKE = "e2e-live-%@premise-labs.dev"

STRICT_USER = "e2e-live-8506988a@premise-labs.dev"
RESIDUE_USER = "e2e-live-3781-e2a26d39@premise-labs.dev"  # the #3781 account
RESIDUE_TEAM = "e2e-live-3781-beefcafe@premise-labs.dev"
RESIDUE_TEAM_ID = "436168ff58d4c1e9a4b2f3d5a1"


# ── 1. parity by declaration ────────────────────────────────────────────────


def test_guard_shape_is_identical_in_detector_and_remediator() -> None:
    """The detector's shape oracle and the remediator's Guard A are ONE rule.

    The two scripts are free to spell the regex separately (no import coupling),
    but they must never *differ*: a divergence between the shape oracle and the
    guard would let a row be classified one way and acted on another. (#5971's
    own root cause was not a divergent shape — both regexes were already
    identical — but the SCOPE mismatch the residue tests below pin.)
    """
    assert detector.EMAIL_RE.pattern == remediator.EMAIL_RE.pattern
    # And the shape is the strict one (not an accidental wildcard): the guard is
    # what keeps a non-test row out of the purge.
    assert detector.EMAIL_RE.pattern == r"^e2e-live-[0-9a-f]{8}@premise-labs\.dev$"


def test_both_scripts_scope_on_the_same_namespace_literal() -> None:
    for module in (detector, remediator):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert NAMESPACE_LIKE in source, f"{module.__name__} lost the namespace literal"


# ── 2. the detector separates the guard residue ─────────────────────────────


def _detector_run_sql(users, teams=()):
    """Fake detector SQL driver: one query per table, keyed on its FROM clause."""

    def run_sql(query: str) -> list[dict]:
        if "FROM public.organizations" in query:
            return list(teams)
        if "FROM auth.users" in query:
            return list(users)
        raise AssertionError(f"unexpected detector query: {query[:120]}")

    return run_sql


def test_counts_classifies_guard_residue_separately() -> None:
    users = [
        {"id": "u-1", "email": STRICT_USER, "created_at": "2026-09-28"},
        {"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"},
    ]
    counts, emails, shape_clean, residue = detector._counts(_detector_run_sql(users))

    assert counts["users"] == 2  # BOTH are counted — an in-namespace row is a signal
    assert shape_clean is False
    assert residue == {"users": [RESIDUE_USER]}
    # The full (untruncated) emails stay available: they are the operator's input
    # to the cleanup script, and a truncated address is not actionable.
    assert sorted(emails["users"]) == sorted([STRICT_USER, RESIDUE_USER])


def test_counts_reports_no_residue_when_every_row_matches_the_guard() -> None:
    users = [{"id": "u-1", "email": STRICT_USER, "created_at": "2026-09-28"}]
    counts, _emails, shape_clean, residue = detector._counts(_detector_run_sql(users))
    assert counts["users"] == 1 and shape_clean is True and residue == {}


def test_json_doc_carries_full_emails_and_a_residue_key(monkeypatch, capsys) -> None:
    """The --json doc (the auto-file payload) must be actionable verbatim."""
    users = [
        {"id": "u-1", "email": STRICT_USER, "created_at": "2026-09-28"},
        {"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"},
    ]
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(
        detector, "_mgmt_api_sql", lambda ref, token, query: _detector_run_sql(users)(query)
    )
    monkeypatch.setattr(sys, "argv", ["e2e_live_reconcile.py", "--json"])

    rc = detector.main()
    captured = capsys.readouterr()
    assert rc == 1, "an in-namespace row is a signal — never a green run"
    doc = json.loads(captured.out.strip().splitlines()[-1])
    assert doc["residue"] == {"users": [RESIDUE_USER]}
    assert doc["offenders"]["users"]["emails"] == [STRICT_USER, RESIDUE_USER], (
        "the doc must carry FULL emails — the old truncated form could not be fed "
        "to the cleanup script"
    )
    assert detector._short_email(RESIDUE_USER) in captured.err, (
        "the human log must name the residue (truncated there by design — the "
        "--json doc carries the FULL form)"
    )
    assert "--include-residue" in captured.err


# ── 3. the remediator enumerates the residue, and promotes it only on request ──


def _remediator_runner(users=(), teams=(), residue_users=(), residue_teams=()):
    """Fake 2146 SQL runner; records every query so window scoping is assertable."""
    seen: list[str] = []

    def runner(query: str) -> list[dict]:
        seen.append(query)
        q = " ".join(query.split())
        if "api_keys" in q and "UNION ALL" in q:
            return []
        residue = "!~" in q
        if "FROM public.organizations" in q:
            return list(residue_teams) if residue else list(teams)
        if "FROM auth.users" in q:
            return list(residue_users) if residue else list(users)
        raise AssertionError(f"unexpected 2146 query: {q[:120]}")

    runner.seen = seen  # type: ignore[attr-defined]
    return runner


def _args(**overrides):
    base = dict(
        all_e2e_live=False,
        include_residue=False,
        window_start=remediator.WINDOW_START,
        window_end=remediator.WINDOW_END,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_enumerate_reports_residue_without_promoting_it() -> None:
    """The default run REPORTS the residue and does NOT delete it (Guard D)."""
    runner = _remediator_runner(
        users=[{"id": "u-1", "email": STRICT_USER, "created_at": "2026-09-28"}],
        residue_users=[{"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}],
    )
    out = StringIO()
    with redirect_stdout(out):
        manifest = remediator.phase_enumerate(_args(), runner)

    assert manifest["counts"]["users"] == 1, "the strict row is the only delete target"
    assert [u["email"] for u in manifest["users"]] == [STRICT_USER]
    assert manifest["residue"] == {
        "teams": [],
        "users": [{"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}],
    }
    assert manifest["scope"]["include_residue"] is False
    printed = out.getvalue()
    assert "[residue]" in printed and RESIDUE_USER in printed, (
        "the residue must be PRINTED regardless — silence is what made #5971 "
        "unresolvable for two weeks"
    )
    assert "NOT in the delete set" in printed


def test_include_residue_promotes_rows_into_the_delete_set() -> None:
    runner = _remediator_runner(
        residue_users=[{"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}],
        residue_teams=[
            {
                "id": RESIDUE_TEAM_ID,
                "name": "e2e-live-3781-beefcafe",
                "email": RESIDUE_TEAM,
                "graph_name": f"team_{RESIDUE_TEAM_ID}",
                "created_at": "2026-09-17",
                "deleted_at": None,
            }
        ],
    )
    out = StringIO()
    with redirect_stdout(out):
        manifest = remediator.phase_enumerate(_args(include_residue=True), runner)

    assert manifest["scope"]["include_residue"] is True
    assert [u["email"] for u in manifest["users"]] == [RESIDUE_USER]
    assert [t["email"] for t in manifest["teams"]] == [RESIDUE_TEAM]
    assert manifest["counts"]["users"] == 1 and manifest["counts"]["teams"] == 1
    # The promoted team's children are enumerated too (deleted children-first by
    # phase_db / dropped from the FalkorDB manifest) — a promoted team without its
    # children would strand them.
    assert any(RESIDUE_TEAM_ID in q and "UNION ALL" in q for q in runner.seen)
    # The residue record survives even when promoted (it is the durable log).
    assert manifest["residue"]["users"][0]["email"] == RESIDUE_USER
    assert "INCLUDED via --include-residue" in out.getvalue()


def test_enumerate_fails_closed_if_a_residue_row_matches_the_guard() -> None:
    """A "residue" row that matches Guard A means the exclusion logic is broken.

    Not a row to skip quietly: the two predicates disagreeing is how a purge ends
    up scoped by something nobody declared.
    """
    runner = _remediator_runner(
        users=[{"id": "u-1", "email": STRICT_USER, "created_at": "2026-09-28"}],
        residue_users=[{"id": "u-9", "email": STRICT_USER, "created_at": "2026-09-28"}],
    )
    with pytest.raises(remediator.OpError, match="GUARD FAIL"):
        remediator.phase_enumerate(_args(), runner)


def test_default_window_excludes_a_recurrence() -> None:
    """The window is a red-window artifact; a recurrence is NEWER than it.

    Pins the second half of #5971's scope mismatch: strict-shape rows created
    after the historical window (the whole #2189 use case) are invisible to a
    plain ``--phase enumerate``'s DELETE set, which is why the reconcile issue's
    remediation now names ``--all-e2e-live``. (A residue row is NOT invisible —
    it is enumerated all-time and printed; only its deletion needs
    ``--include-residue``.)
    """
    default = _remediator_runner(users=[])
    with redirect_stdout(StringIO()):
        remediator.phase_enumerate(_args(), default)
    scoped = " ".join(default.seen)
    assert remediator.WINDOW_START in scoped and remediator.WINDOW_END in scoped

    wide = _remediator_runner(users=[])
    with redirect_stdout(StringIO()):
        remediator.phase_enumerate(_args(all_e2e_live=True), wide)
    assert remediator.WINDOW_START not in " ".join(wide.seen), (
        "--all-e2e-live is the recurrence path: it drops the window entirely"
    )


def test_residue_queries_are_all_time_and_negate_the_guard() -> None:
    """Guard D's SQL: namespace LIKE, NOT the guard shape, and no window clause."""
    for sql in (remediator.RESIDUE_TEAMS_SQL, remediator.RESIDUE_USERS_SQL):
        assert NAMESPACE_LIKE in sql
        assert "!~" in sql, "the residue predicate must NEGATE the guard, never widen it"
        # Pin the PREDICATE, not just the operator: a widened literal such as
        # `!~ '^e2e-live-.*@premise-labs\.dev$'` still contains `!~` yet shrinks
        # the residue back to empty and re-opens #5971 — and the fail-closed
        # check above cannot catch it (a too-wide residue regex yields FEWER
        # rows). The SQL literal must be the guard pattern verbatim.
        assert remediator.EMAIL_RE.pattern in sql, (
            "the residue SQL must negate the SAME literal the guard matches"
        )
        assert "created_at >=" not in sql, (
            "residue is all-time — the live example (#3781) postdates WINDOW_END"
        )
        assert "__WINDOW__" not in sql


# ── 4. the destructive manifest-driven phases must not go silent ────────────


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_residue_only_recurrence_uses_its_own_manifest_and_keeps_the_old_one(
    tmp_path, monkeypatch
) -> None:
    """A recurrence with no Guard-A (strict-shape) teams must NOT discard the prior
    FalkorDB drop list.

    The live #5971 case is `users=4 teams=0`, so a recurrence whose rows are all
    users enumerates an EMPTY team list. The prior manifest's `teams` is the drop
    record the FalkorDB companion reads
    (`2146_falkordb_graph_cleanup.py` reads `manifest["teams"]`
    and returns 0 on an empty list), so the clobber guard refuses — by design.
    The recurrence reaches its rows by enumerating to its OWN --manifest, which
    is the runbook §6.1 procedure.
    """
    prior_path = tmp_path / "2146-e2e-live-orphans.manifest.json"
    prior_teams = [{"id": f"old-{i}", "graph_name": f"team_{i:026x}"} for i in range(154)]
    _write_manifest(prior_path, {"teams": prior_teams})

    runner = _remediator_runner(
        residue_users=[{"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}]
    )
    monkeypatch.setattr(remediator, "_make_sql_runner", lambda args: runner)
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "enumerate",
         "--all-e2e-live", "--manifest", str(prior_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 2 and "REFUSING" in out.getvalue(), (
        "a 0-team enumeration must not overwrite a manifest holding graph targets"
    )
    assert json.loads(prior_path.read_text(encoding="utf-8"))["teams"] == prior_teams, (
        "the FalkorDB drop list must survive the refusal"
    )

    # The recurrence path: its own manifest, same residue, and it proceeds.
    recurrence_path = tmp_path / "2146-recurrence.manifest.json"
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "enumerate",
         "--all-e2e-live", "--manifest", str(recurrence_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 0, "a separate manifest is the documented recurrence path"
    written = json.loads(recurrence_path.read_text(encoding="utf-8"))
    assert written["residue"]["users"][0]["email"] == RESIDUE_USER
    assert RESIDUE_USER in out.getvalue()
    # And the historical list is still intact afterwards.
    assert json.loads(prior_path.read_text(encoding="utf-8"))["teams"] == prior_teams


def test_a_promoted_residue_team_cannot_route_around_the_clobber_guard(
    tmp_path, monkeypatch
) -> None:
    """--include-residue must not make a 0-team enumeration look non-empty.

    The guard has to test the PRE-promotion (Guard-A) team list. A promoted
    residue TEAM makes `manifest["teams"]` non-empty, and a guard reading the
    promoted list would overwrite a prior manifest holding 154 team graph names
    — silently discarding the drop record the FalkorDB companion reads.
    """
    prior_path = tmp_path / "2146-e2e-live-orphans.manifest.json"
    prior_teams = [{"id": f"old-{i}", "graph_name": f"team_{i:026x}"} for i in range(154)]
    _write_manifest(prior_path, {"teams": prior_teams})
    residue_team = {
        "id": RESIDUE_TEAM_ID, "name": "e2e-live-3781-beefcafe", "email": RESIDUE_TEAM,
        "graph_name": f"team_{RESIDUE_TEAM_ID}", "created_at": "2026-09-17",
        "deleted_at": None,
    }
    runner = _remediator_runner(residue_teams=[residue_team])
    monkeypatch.setattr(remediator, "_make_sql_runner", lambda args: runner)
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "enumerate",
         "--all-e2e-live", "--include-residue", "--manifest", str(prior_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 2 and "REFUSING" in out.getvalue(), (
        "a promoted residue team must not bypass the clobber guard"
    )
    assert json.loads(prior_path.read_text(encoding="utf-8"))["teams"] == prior_teams

    # With no prior manifest the promoted residue team is written normally.
    fresh_path = tmp_path / "2146-recurrence.manifest.json"
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "enumerate",
         "--all-e2e-live", "--include-residue", "--manifest", str(fresh_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 0
    written = json.loads(fresh_path.read_text(encoding="utf-8"))
    assert [t["email"] for t in written["teams"]] == [RESIDUE_TEAM]
    assert written["scope"]["guard_a_teams"] == 0
    assert written["scope"]["include_residue"] is True


def test_genuinely_empty_reenumeration_still_refuses(tmp_path, monkeypatch) -> None:
    """The #2146 protection survives: a truly empty re-enumeration still refuses."""
    manifest_path = tmp_path / "2146.manifest.json"
    _write_manifest(manifest_path, {"teams": [{"id": "old"}] * 154})
    monkeypatch.setattr(remediator, "_make_sql_runner", lambda args: _remediator_runner())
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "enumerate",
         "--all-e2e-live", "--manifest", str(manifest_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 2 and "REFUSING" in out.getvalue()


def test_manifest_driven_delete_phase_reports_the_residue(tmp_path, monkeypatch) -> None:
    """`--phase users|db` delete from the manifest and must not be silent about it."""
    manifest_path = tmp_path / "2146.manifest.json"
    _write_manifest(manifest_path, {
        "scope": {"all_e2e_live": True, "include_residue": False,
                  "window_start": None, "window_end": None,
                  "email_regex": remediator.EMAIL_RE.pattern},
        "counts": {"teams": 0, "users": 1},
        "residue": {"teams": [], "users": [
            {"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}]},
        "teams": [],
        "users": [{"id": "u-1", "email": STRICT_USER, "created_at": "2026-09-28"}],
    })
    monkeypatch.setattr(remediator, "_make_sql_runner", lambda args: _remediator_runner())
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "users",
         "--manifest", str(manifest_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 0
    printed = out.getvalue()
    assert "[residue]" in printed and RESIDUE_USER in printed, (
        "the destructive phases must state the residue they are NOT touching"
    )
    assert "NOT in the delete set" in printed


def test_include_residue_is_never_silently_inert_on_a_manifest_phase(
    tmp_path, monkeypatch
) -> None:
    """`--include-residue` on a manifest-driven phase cannot promote anything."""
    manifest_path = tmp_path / "2146.manifest.json"
    _write_manifest(manifest_path, {
        "scope": {"all_e2e_live": True, "include_residue": False},
        "counts": {},
        "residue": {"teams": [], "users": [
            {"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}]},
        "teams": [], "users": [],
    })
    monkeypatch.setattr(remediator, "_make_sql_runner", lambda args: _remediator_runner())
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "db",
         "--include-residue", "--manifest", str(manifest_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 0
    assert "no effect on --phase db" in out.getvalue()
    assert "NOT in the delete set" in out.getvalue()


def test_the_inert_note_does_not_fire_when_the_manifest_already_promoted(
    tmp_path, monkeypatch
) -> None:
    """The redundant-flag case: the manifest already promoted the residue.

    The note must not contradict the `INCLUDED via --include-residue` line
    printed for the same rows in the same run.
    """
    manifest_path = tmp_path / "2146.manifest.json"
    _write_manifest(manifest_path, {
        "scope": {"all_e2e_live": True, "include_residue": True},
        "counts": {},
        "residue": {"teams": [], "users": [
            {"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}]},
        "teams": [],
        "users": [{"id": "u-2", "email": RESIDUE_USER, "created_at": "2026-09-17"}],
    })
    monkeypatch.setattr(remediator, "_make_sql_runner", lambda args: _remediator_runner())
    monkeypatch.setattr(
        sys, "argv",
        ["2146_e2e_live_orphan_cleanup.py", "--phase", "users",
         "--include-residue", "--manifest", str(manifest_path)],
    )
    out = StringIO()
    with redirect_stdout(out):
        rc = remediator.main()
    assert rc == 0
    printed = out.getvalue()
    assert "INCLUDED via --include-residue" in printed
    assert "no effect on --phase" not in printed, (
        "the note must not contradict the INCLUDED line about the same rows"
    )
