"""#13205 — tenant-graph provisioning: no double prefix, and delete means deprovision.

Three defects, one class: **a graph nobody can name is a graph nobody can reap.**

1. The name composer concatenated unconditionally (``f"org_{namespace}"``, and
   ``f"team_{namespace}"`` before #3543), so an input that already carried the
   prefix minted a second one. The production instance carried
   ``team_team_3326a01ea34ae595d84de5d8f9`` — unnameable by every resolver,
   because each one composes from a BARE 26-hex org id. Measured cost of one such
   graph: ~1.46 MB of resident RAM (216.4 MB freed for 148 graphs), and it is only
   visible on DELETE — a create-probe reads ~346 KiB because the allocator reuses
   held pages. **The create-probe is a false negative; do not gate on it.**

2. The hard-delete purge dropped exactly ONE name (the org's stored default), so
   every custom ``org_{org_id}_{gid}`` graph — and every legacy-spelled one —
   outlived the row that owned it. That is why 148 empty graphs survived their
   tests.

3. The sweeper that reclaims them has to be safe BY CONSTRUCTION: dry-run by
   default, only test/agent owners, only genuinely empty graphs, and it must
   REFUSE — never guess — when the owner cannot be resolved. An orphan is an
   adjudication, not a cleaning job.

The tests below fail on the pre-fix code: (1) returns ``org_team_team_x``, (2)
drops ``[org_x]`` where it must drop the whole owned set, (3) has no tool at all.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_tool():
    """Import ``tools/reap_empty_test_graphs.py`` by path (``tools/`` is not a
    package, and this keeps the import side-effect-free)."""
    path = ROOT / "tools" / "reap_empty_test_graphs.py"
    spec = importlib.util.spec_from_file_location("reap_empty_test_graphs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reaper = _load_tool()

DOUBLE_PREFIXED = "team_team_3326a01ea34ae595d84de5d8f9"
BARE = "3326a01ea34ae595d84de5d8f9"


# ── 1. the composer is idempotent ────────────────────────────────────────────

class TestNoDoublePrefix:
    def test_sdk_composition_cannot_mint_a_second_prefix(self):
        """THE regression: a prefixed input must not compose to a double prefix.

        Pre-fix ``_derive_graph_name(namespace="team_x")`` returned
        ``"org_team_x"``, and the pre-#3543 composer produced exactly the
        ``team_team_…`` name measured on the instance.
        """
        from tortoise.sdk import _derive_graph_name

        assert _derive_graph_name(namespace=DOUBLE_PREFIXED,
                                  graph_name=None) == f"org_{BARE}"
        assert _derive_graph_name(namespace=f"team_{BARE}",
                                  graph_name=None) == f"org_{BARE}"
        assert _derive_graph_name(namespace=f"org_{BARE}",
                                  graph_name=None) == f"org_{BARE}"
        assert _derive_graph_name(namespace=BARE, graph_name=None) == f"org_{BARE}"

    @pytest.mark.parametrize("value", [
        f"org_{BARE}", f"team_{BARE}", f"team_team_{BARE}",
        f"org_org_{BARE}", f"org_team_{BARE}", f"team_org_{BARE}",
    ])
    def test_composition_is_idempotent(self, value):
        """Applying the composer to its OWN output must be the identity.

        This is the property, not an example: any fix that strips only one prefix
        returns ``org_team_x`` from ``team_team_x`` and then ``org_team_x`` again
        — idempotent, but still a name no lookup derives. Only the BARE id is a
        fixed point of the spelling family, so the test also pins the fixed point.
        """
        from tortoise.sdk import canonical_org_graph_name

        once = canonical_org_graph_name(value)
        assert canonical_org_graph_name(once) == once
        assert once == f"org_{BARE}"

    def test_the_registry_control_plane_is_not_canonicalised(self):
        """The prefix rule must not swallow the reserved control-plane namespace
        — ``registry`` is a namespace of its own, not an org id."""
        from tortoise.sdk import _derive_graph_name

        assert (_derive_graph_name(namespace="registry", graph_name=None)
                == "registry_tortoise")

    def test_test_namespace_families_are_unchanged(self):
        """The strip runs in the ORG arm only: the test families keep the
        isolation the suite depends on (a test graph that fell out of the
        ``test_*`` family would escape ``_assert_test_graph``)."""
        from tortoise.sdk import _derive_graph_name

        assert _derive_graph_name(namespace="test_x", graph_name=None) == "test_x_tortoise"
        assert _derive_graph_name(namespace="test-x", graph_name=None) == "test_x_tortoise"
        assert _derive_graph_name(namespace="tortoise_testing",
                                  graph_name=None) == "tortoise_testing_tortoise"

    def test_explicit_graph_name_still_wins_verbatim(self):
        """An explicit ``graph_name`` is a full DB name and must never be
        re-composed — the #2114 seam."""
        from tortoise.sdk import _derive_graph_name

        assert _derive_graph_name(namespace=BARE,
                                  graph_name=f"org_{BARE}_g_abc") == f"org_{BARE}_g_abc"

    def test_the_admission_mirror_cannot_disagree(self):
        """``hosted_api._graph_name_for_namespace`` restates the mapping for the
        I/O-free admission path. If it kept its own concatenation, the admission
        key would bucket a request by a graph the request never opens (#3365)."""
        from tortoise.hosted_api import _graph_name_for_namespace
        from tortoise.sdk import _derive_graph_name

        for ns in [BARE, f"org_{BARE}", f"team_{BARE}", f"team_team_{BARE}",
                   "team-001", "test_x", "test-x", "registry"]:
            assert _graph_name_for_namespace(ns) == _derive_graph_name(
                namespace=ns, graph_name=None), ns
        assert _graph_name_for_namespace(f"team_{BARE}") == f"org_{BARE}"


# ── 2. deleting an org deprovisions ALL of its graphs ────────────────────────

class TestDeprovisionOnDelete:
    def _fake_cp(self, rows, graphs=None):
        """A control plane returning the given organization + graphs rows.

        ``query`` answers by table, so the test drives the REAL enumerator rather
        than a monkeypatched one — the safety filter is the thing under test.
        """
        class _CP:
            def query(self, table, **kw):
                if table == "organizations":
                    return rows
                if table == "graphs":
                    return graphs or []
                return []
        return _CP()

    def test_every_graph_the_org_owns_is_in_the_deprovision_list(self, monkeypatch):
        """The pre-fix purge dropped the stored default ONLY, so a custom
        ``org_{id}_{gid}`` graph outlived the row. Three spellings + one custom
        row must all be present."""
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "org_o1"}],
                                graphs=[{"org_id": "o1", "namespace": "org_o1_g_abc"}]))
        names = ha._org_graph_names("o1", "org_o1")
        assert names[0] == "org_o1"          # the stored default stays FIRST
        assert "org_o1" in names
        assert "team_o1" in names            # pre-#3543 spelling, still on disk
        assert "org_o1_g_abc" in names       # the custom graph: the actual leak
        assert len(names) == len(set(names))

    def test_a_foreign_or_malformed_row_is_never_dropped(self, monkeypatch):
        """The enumerator supplies CANDIDATES; ownership decides. A corrupt
        ``graphs`` row must not make a purge delete another tenant's graph."""
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "org_o1"}],
                                graphs=[{"org_id": "o1", "namespace": "org_o2"},
                                        {"org_id": "o1", "namespace": "org_o1x_g_1"},
                                        {"org_id": "o1", "namespace": None}]))
        names = ha._org_graph_names("o1", "org_o1")
        assert names == ["org_o1", "team_o1"], names

    def test_a_legacy_stored_name_is_dropped_alongside_the_canonical_one(self, monkeypatch):
        """A pre-#3543 org stores ``team_{id}``. Both spellings are dropped: the
        graph its data is actually in, and the one every modern lookup derives."""
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "team_o1"}]))
        assert ha._org_graph_names("o1", "team_o1") == ["team_o1", "org_o1"]

    def test_an_unreadable_enumeration_still_drops_the_derived_names(self, monkeypatch):
        """A listing failure must not block the erasure of the derived names —
        and must not silently widen it either."""
        from tortoise import hosted_api as ha

        class _Broken:
            def query(self, *a, **k):
                raise RuntimeError("postgrest down")

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: _Broken())
        assert ha._org_graph_names("o1", "org_o1") == ["org_o1", "team_o1"]

    def test_strict_deprovision_drops_each_name_and_is_rerunnable(self, monkeypatch):
        """The strict seam the purge calls: one drop per owned name, and a second
        run (every graph already gone) is a success, not a failure — the #926
        retry anchor must converge."""
        from tortoise import hosted_api as ha

        dropped: list[str] = []
        monkeypatch.setattr(ha, "_org_graph_names",
                            lambda org_id, graph_name=None: ["org_o1", "team_o1"])
        monkeypatch.setattr(ha, "_drop_org_graph_strict",
                            lambda org_id, graph_name=None: dropped.append(graph_name))
        assert ha._drop_org_graphs_strict("o1", "org_o1") == ["org_o1", "team_o1"]
        assert dropped == ["org_o1", "team_o1"]
        dropped.clear()
        ha._drop_org_graphs_strict("o1", "org_o1")
        assert dropped == ["org_o1", "team_o1"]


# ── 3. the reaper: safe by construction ──────────────────────────────────────

def _org(org_id, name="acme", email=None):
    return {"id": org_id, "name": name, "email": email, "graph_name": f"org_{org_id}"}


class TestReaperRefusesRatherThanGuesses:
    """Every gate, asserted as a REFUSAL where the input is ambiguous."""

    def test_an_orphan_graph_is_refused_not_reaped(self):
        """A graph with no ``organizations`` row is the one thing a cleaner must
        never touch: ``x``, ``tortoise``, ``g_0bcdb2…`` and the ``team_*`` names
        with no org row all live here, and they need adjudication."""
        for name in ["x", "g_0bcdb207ef7336c3", "team_95afa6a07a3afbf0228700a4de",
                     DOUBLE_PREFIXED]:
            bucket, reason = reaper.classify_graph(
                name, org_index={}, node_count=0)
            assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, name
            assert "orphan" in reason

    def test_reserved_control_plane_graphs_are_refused_even_if_empty(self):
        for name in ["registry_control_plane", "registry_tortoise", "tortoise"]:
            bucket, _ = reaper.classify_graph(
                name, org_index={"registry_tortoise": _org("x", "agent-x")},
                node_count=0)
            assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, name

    def test_an_unresolvable_name_is_never_assumed_to_be_test(self):
        """Order matters: ownership is decided BEFORE the test marker, so an
        unresolvable graph never gets filed as "some org's, not test" — which
        would read like a decision about an owner that does not exist."""
        bucket, _ = reaper.classify_graph(
            "org_nobody", org_index={"org_someone": _org("o", "agent-x")},
            node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED

    def test_an_unreadable_emptiness_read_is_a_refusal(self):
        bucket, reason = reaper.classify_graph(
            "org_o1", org_index={"org_o1": _org("o1", "agent-signup")},
            node_count=None)
        assert bucket == reaper.BUCKET_REFUSED_UNREADABLE
        assert "never assumed" in reason

    def test_a_customer_org_is_kept_even_when_empty(self):
        """The whole safety property: a real external signup's empty graph is
        NOT a leak (the issue kept 13 of them deliberately)."""
        for org in [_org("o1", "Daylight Computer Co"),
                    _org("o2", "xware"),
                    _org("o3", "Simone", "simone@gmail.com")]:
            bucket, _ = reaper.classify_graph(
                f"org_{org['id']}", org_index={f"org_{org['id']}": org},
                node_count=0)
            assert bucket == reaper.BUCKET_KEPT_NOT_TEST_ORG, org

    def test_a_non_empty_test_graph_is_kept(self):
        """Data is a data question. ``Apresto Internal Test`` held 47 MB of real
        content — an empty-only gate is what keeps a cleaner from being an
        incident."""
        bucket, reason = reaper.classify_graph(
            "org_o1", org_index={"org_o1": _org("o1", "agent-x")}, node_count=3)
        assert bucket == reaper.BUCKET_KEPT_NOT_EMPTY
        assert "3 node(s)" in reason

    @pytest.mark.parametrize("org,expected", [
        (_org("a", "agent-signup"), True),
        (_org("a", "agent_signup"), True),
        (_org("a", "e2e-first-ten-minutes"), True),
        (_org("a", "e2e-live-xyz"), True),
        (_org("a", "vgate-prod"), True),
        # "Agent Test" is one of the orgs the issue counted as a test org, and it
        # is NOT matched by the name marker (space, not a separator) — it is
        # named explicitly with --test-org instead (see the tool's docstring).
        (_org("a", "Agent Test"), False),
        (_org("a", "acme", "e2e-live-3781aabb@premise-labs.dev"), True),
        # NOT test: a customer who happens to use the letters, and our own
        # non-test domain.
        (_org("a", "E2E Logistics Ltd"), False),
        (_org("a", "agentic systems"), False),
        (_org("a", "agent ops"), False),
        (_org("a", "acme", "founder@premise-labs.dev"), False),
        (_org("a", "acme", "someone@gmail.com"), False),
    ])
    def test_the_test_org_marker_is_narrow_and_anchored(self, org, expected):
        assert reaper.is_test_org(org) is expected, org

    def test_an_explicit_org_id_is_the_only_override(self):
        """No wildcard, ever: an operator names an exact org id, and the marker
        test still governs everything else."""
        org = _org("01J8ZZK4M7HQ3X9Z8R2K6VWPB", "acme")
        assert reaper.is_test_org(org) is False
        assert reaper.is_test_org(org, frozenset({"01J8ZZK4M7HQ3X9Z8R2K6VWPB"})) is True
        assert reaper.is_test_org(org, frozenset({"prefix-match", "01J8"})) is False

    def test_no_control_plane_means_refuse_everything(self, monkeypatch):
        """A self-host lane has no cross-tenant leak to clean, and ownership
        cannot be resolved — so NOTHING is reapable, and the run says so rather
        than reporting "0 reapable" over an unresolvable instance."""
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: False)
        with pytest.raises(reaper.Refusal, match="no control plane"):
            reaper.build_report()

    def test_dry_run_is_the_default_and_deletes_nothing(self, monkeypatch, capsys):
        """The flag changes ONLY the last step: without ``--execute`` the same
        classification runs and mutates nothing."""
        calls: list[str] = []

        class _Graph:
            def __init__(self, name):
                self.name = name

            def query(self, *a, **k):
                rows = type("R", (), {"result_set": [(0,)]})()
                return rows

            def delete(self):
                calls.append(self.name)

        class _DB:
            def list_graphs(self):
                return ["org_o1", "org_customer", "orphan"]

            def select_graph(self, name):
                return _Graph(name)

        class _Proj:
            db = _DB()

        monkeypatch.setattr(reaper, "build_report",
                            lambda **kw: _real_report(_Proj()))
        monkeypatch.setattr("tortoise.hosted_api._make_sdk", _boom)

        assert reaper.main([]) == 0
        assert calls == [], "dry run deleted something"
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert reaper.BUCKET_REAPABLE in out


def _boom(*a, **k):
    raise AssertionError("dry run must not construct the data-plane SDK")


def _real_report(proj):
    """The real classifier over a fake projection and a fake org index."""
    org_index = {
        "org_o1": _org("o1", "agent-signup"),
        "org_customer": _org("c1", "Daylight Computer Co"),
    }
    rows = []
    for name in proj.db.list_graphs():
        needs = name in org_index
        count = 0 if needs else None
        bucket, reason = reaper.classify_graph(
            name, org_index=org_index, node_count=count)
        rows.append({"graph": name, "bucket": bucket, "reason": reason,
                     "org_id": (org_index.get(name) or {}).get("id"),
                     "org_name": (org_index.get(name) or {}).get("name"),
                     "node_count": count})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    return {"graphs": rows, "counts": counts, "total": len(rows)}


class TestReaperExecute:
    def test_execute_deletes_only_the_reapable_bucket(self, monkeypatch):
        deleted: list[str] = []

        class _Graph:
            def __init__(self, name):
                self.name = name

            def delete(self):
                deleted.append(self.name)

        class _DB:
            def select_graph(self, name):
                return _Graph(name)

        class _Proj:
            db = _DB()

        monkeypatch.setattr("tortoise.hosted_api._make_sdk",
                            lambda *a, **k: type(
                                "S", (), {"_get_proj": lambda self: _Proj()})())
        report = _real_report(type("P", (), {
            "db": type("D", (), {"list_graphs": lambda self: [
                "org_o1", "org_customer", "orphan"]})()})())
        outcome = reaper.reap(report, proj=_Proj())
        assert deleted == ["org_o1"], deleted
        assert [o["graph"] for o in outcome] == ["org_o1"]
        assert all(o["deleted"] for o in outcome)
