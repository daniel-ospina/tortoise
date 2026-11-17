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

import contextlib
import importlib.util
import logging
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
    def _fake_cp(self, rows, graphs=None, complete=True):
        """A control plane returning the given organization + graphs rows.

        ``query``/``query_all`` answer by table, so the test drives the REAL
        enumerator rather than a monkeypatched one — the safety filter is the
        thing under test. ``graphs`` is read through ``query_all`` (a single
        page is silently capped at `max_rows`, so a truncated walk must be
        refused rather than silently dropping the tail of the deprovision list).
        """
        class _CP:
            def query(self, table, **kw):
                if table == "organizations":
                    return rows
                if table == "graphs":
                    return graphs or []
                return []

            def query_all(self, table, **kw):
                # Mirror the real seam's precondition (``supabase_control.
                # query_all`` raises when the cursor column is not selected):
                # this diff makes `_org_graph_names` DEPEND on it by changing
                # ``select=["namespace"]`` to ``select=["id", "namespace"]``,
                # so dropping ``id`` again must red here, not ship a walk the
                # real store refuses.
                assert kw.get("cursor") == "id", kw.get("cursor")
                assert "id" in (kw.get("select") or []), kw.get("select")
                # ⛔ DELEGATED — `FakeControlPlane._certifiable` is the ONE statement
                # of the certificate, and it now mirrors BOTH of production's
                # branches (an empty page certifies; a non-empty one needs at least
                # one cursor). Restating it here is what produced the inverse of this
                # PR's bug: the line used to be `all(...)`, which refused a MIXED page
                # (the real walk advances from its cursored rows), so
                # `_org_graph_names` raised and the org could NEVER be purged.
                #
                # This line has now been wrong in BOTH directions, which is why all
                # three arms are pinned: `test_a_MIXED_page_still_certifies_…`,
                # `test_a_page_with_NO_cursor_anywhere_is_REFUSED`, and the
                # pre-existing empty-page arm in
                # `test_a_legacy_stored_name_is_dropped_alongside_the_canonical_one`.
                # The SHARED double's own three arms are pinned separately by
                # `TestTheSharedDoubleDerivesItsCertificate` (#13249 round 14).
                # `complete` still lets a test SAY "this walk was truncated"
                # (`test_a_truncated_graphs_walk_…`).
                from tests.fake_control_plane import FakeControlPlane

                rows = graphs or []
                certified = FakeControlPlane._certifiable(rows, None, "id")
                return rows, bool(complete and certified)
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
                                graphs=[{"id": "g_abc", "org_id": "o1", "namespace": "org_o1_g_abc"}]))
        names = ha._org_graph_names("o1", "org_o1")
        assert "team_o1" in names            # pre-#3543 spelling, still on disk
        assert "org_o1_g_abc" in names       # the custom graph: the actual leak
        assert "org_o1" in names             # the canonical default
        assert names[-1] == "org_o1"         # the bind target, LAST (see below)
        assert len(names) == len(set(names))

    def test_the_bind_target_is_dropped_LAST(self, monkeypatch):
        """⛔ ORDER IS A CORRECTNESS PROPERTY, not tidiness (#13205 review, P0).

        Every drop binds ``org_{org_id}`` (``_drop_org_graph_impl`` calls
        ``_make_sdk(namespace=org_id)._get_proj()``), and binding a graph the
        store does not hold MATERIALISES it — so a list that deleted the
        canonical name and then bound it again on the NEXT name would end the
        purge with the tenant's graph still present. Measured on the live lane
        (``falkordb-server:v4.20.6``): delete → bind → the graph is back in
        ``GRAPH.LIST`` with its node. The bind target must therefore be the last
        element, which is what makes every bind precede its delete.
        """
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "team_o1"}],
                                graphs=[{"id": "g_1", "org_id": "o1", "namespace": "org_o1_g_1"},
                                        {"id": "g_2", "org_id": "o1", "namespace": "org_o1_g_2"}]))
        names = ha._org_graph_names("o1", "team_o1")
        assert names[-1] == "org_o1", names
        assert names.count("org_o1") == 1, names
        for other in ("team_o1", "org_o1_g_1", "org_o1_g_2"):
            assert other in names and names.index(other) < len(names) - 1, names

    def test_a_foreign_or_malformed_row_is_never_dropped(self, monkeypatch):
        """The enumerator supplies CANDIDATES; ownership decides. A corrupt
        ``graphs`` row must not make a purge delete another tenant's graph."""
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "org_o1"}],
                                graphs=[{"id": "g_a", "org_id": "o1", "namespace": "org_o2"},
                                        {"id": "g_b", "org_id": "o1", "namespace": "org_o1x_g_1"},
                                        {"id": "g_c", "org_id": "o1", "namespace": None}]))
        names = ha._org_graph_names("o1", "org_o1")
        assert names == ["team_o1", "org_o1"], names

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
    def test_a_truncated_graphs_walk_REFUSES_rather_than_dropping_the_tail(self, monkeypatch):
        """⛔ A single page is silently capped at the deployment's `max_rows`, so
        an org owning more graphs than one page (reachable: `max_graphs_per_team`
        is `null` — unlimited — on pro and team, #10743) would lose the TAIL of its custom graphs from the
        deprovision list — and `purge_org_control_plane` would then delete the
        rows that named them, making them unnameable and unreapable forever
        (#13205 review round 6). The walk is `query_all`, and an uncertified one
        raises so the org row survives as the retry anchor."""
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "org_o1"}],
                                graphs=[{"id": "g_1", "namespace": "org_o1_g_1"}],
                                complete=False))
        with pytest.raises(RuntimeError, match="could not be certified complete"):
            ha._org_graph_names("o1", "org_o1")

    def test_a_MIXED_page_still_certifies_so_the_org_CAN_be_purged(self, monkeypatch):
        """⛔ The certificate must be as LENIENT as production, not stricter
        (#13205 review round 13).

        The real walk's rule is ``advanceable = [c for c in cursors if c]; if not
        advanceable: break`` — so a page carrying at least ONE cursored row still
        ADVANCES, and the uncursored rows stay in ``rows``. Requiring EVERY row to
        carry one (an ``all(...)`` certifier) reports such a page as truncated,
        ``_org_graph_names`` raises, and the purge aborts — the INVERSE of the
        leak this PR exists to fix: an org that can never be purged at all.

        Both graphs still get dropped, because ownership is decided by
        ``namespace``; the uncursored row only declines to help the walk ADVANCE,
        it does not make itself undroppable."""
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "org_o1"}],
                                graphs=[{"id": "g_1", "namespace": "org_o1_g_1"},
                                        # no `id`: production keeps this row and
                                        # still advances from its sibling.
                                        {"namespace": "org_o1_g_2"}]))
        names = ha._org_graph_names("o1", "org_o1")
        assert "org_o1_g_1" in names, names
        assert "org_o1_g_2" in names, names
        assert names[-1] == "org_o1", names

    def test_a_page_with_NO_cursor_anywhere_is_REFUSED(self, monkeypatch):
        """The OTHER half of the same rule (#13205 review round 13), so the
        certificate cannot drift either way.

        A NON-empty page where not one row carries a cursor leaves
        ``advanceable`` empty, so the real walk breaks with ``exhausted`` False and
        CANNOT certify. That is what makes the whole walk refuse rather than
        drop the graphs it happened to see — the direction the derived certificate
        was originally introduced for (three deprovision fixtures shipped rows
        carrying only ``namespace`` and were green because the double certified
        unconditionally)."""
        from tortoise import hosted_api as ha

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: self._fake_cp(
                                [{"id": "o1", "graph_name": "org_o1"}],
                                graphs=[{"namespace": "org_o1_g_1"},
                                        {"namespace": "org_o1_g_2"}]))
        with pytest.raises(RuntimeError, match="could not be certified complete"):
            ha._org_graph_names("o1", "org_o1")

    def test_the_acl_user_walk_reads_EVERY_page_of_graphs(self, monkeypatch):
        """⛔ Review round 7: `_drop_org_acl_users` read ONE PostgREST page.

        `query` is capped at the deployment's `max_rows` (1000) and cannot report
        its own truncation, so an org past one page (reachable:
        `max_graphs_per_team` is `null` on pro and team, #10743) keeps the TAIL of its gids — and a gid names
        a GLOBAL `tenant_<gid>` ACL user, i.e. a LIVE CREDENTIAL, in the very
        purge that then deletes the graphs and the rows naming them. The walk is
        `query_all` (cursor in `select`), which is what drops the second page's
        user here — the class `_org_graph_names` refuses one function up, one step
        earlier in the same purge.

        Mutant: restore `rows = cp.query("graphs", select=["id"], ...)` — `g_2`
        is never enumerated and this reds on `dropped`.
        """
        from tortoise import hosted_api as ha

        dropped: list[str] = []
        monkeypatch.setattr(ha, "_acl_user_drop_hook", dropped.append)
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)

        class _CP:
            """Page 1 only through `query`; the WHOLE set through `query_all`."""

            def query(self, table, **kw):
                return [{"id": "g_1"}] if table == "graphs" else []

            def query_all(self, table, **kw):
                assert kw.get("cursor") == "id", "the walk must key on `id`"
                assert "id" in (kw.get("select") or []), kw.get("select")
                return [{"id": "g_1"}, {"id": "g_2"}], True

        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: _CP())
        ha._drop_org_acl_users("o1")
        assert dropped == ["g_1", "g_2"], (
            "the tail of the org's gids was never enumerated — their live ACL "
            "users outlive the purge that deletes the graphs")

    def test_an_uncertified_acl_walk_DROPS_what_it_saw_and_says_SO(self, monkeypatch, caplog):
        """The policy is best-effort — recorded by C4 in `e2131c6847`, "Best-effort per
        graph (a committed purge never fails on a drop)" — so an uncertified walk
        must NOT raise. It must be LOUD instead: the ids it saw are a LOWER
        BOUND, and the credential on any graph past it outlives every way to name
        it (the operator can only drop it by hand if the bound is announced).

        Mutant: delete the `if not complete:` block — the drop still happens, so
        this reds on the LOG assertion, which is why the assertion is the log and
        not the exception: making this raise would reverse that policy (`e2131c6847`),
        which is a docstring review finding, NOT a decision stated on #2113 (see the
        function's own docstring).
        """
        from tortoise import hosted_api as ha

        dropped: list[str] = []
        monkeypatch.setattr(ha, "_acl_user_drop_hook", dropped.append)
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)

        class _CP:
            def query(self, table, **kw):
                return []

            def query_all(self, table, **kw):
                return [{"id": "g_1"}], False

        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: _CP())
        caplog.set_level(logging.WARNING, logger="tortoise.hosted_api")
        ha._drop_org_acl_users("o1")           # must NOT raise (`e2131c6847`)
        assert dropped == ["g_1"], dropped     # best-effort still drops what it saw
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "LOWER BOUND" in text and "o1" in text, text

    def test_a_seam_without_query_all_is_ANNOUNCED_not_silent(self, monkeypatch, caplog):
        """⛔ THE FAILURE MODE THE DELETED `getattr` SEAM INTRODUCED (#13205
        review round 9, finding 2). Both reads of this table now use ONE bare
        `query_all` shape — deliberately not a `getattr` fallback. A seam that
        predates the walk therefore raises `AttributeError` into the best-effort
        handler below, which logs and returns having dropped NO user.

        Continuing is correct per the policy C4 recorded in `e2131c6847` ("Best-effort
        per graph (a committed purge never fails on a drop)"), but only while it
        is ANNOUNCED: `tenant_<gid>` users are GLOBAL state that nothing else
        names, so the operator can only drop them by hand if the failure is said
        out loud. `test_a_seam_double_without_query_all_still_enumerates` pinned
        the old fallback until it was deleted with the seam; this pins the log
        that replaced it, which is all that stands between the removal and
        silence.

        Mutant: delete the `_logger.warning` in the handler (keep the `return`)
        — reds on the log assertion here, while the sweep still does not raise
        (`e2131c6847`; not a decision stated on #2113).
        """
        from tortoise import hosted_api as ha

        dropped: list[str] = []
        monkeypatch.setattr(ha, "_acl_user_drop_hook", dropped.append)
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)

        class _NoWalker:
            """A control-plane seam from BEFORE the `query_all` walk: `query` only."""

            def query(self, table, **kw):
                return []

        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: _NoWalker())
        caplog.set_level(logging.WARNING, logger="tortoise.hosted_api")
        ha._drop_org_acl_users("o1")       # must NOT raise (`e2131c6847`)
        assert dropped == [], dropped      # nothing was named, nothing dropped
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "ACL-user enumeration failed" in text and "o1" in text, text
        assert "query_all" in text, text   # names the seam that cannot walk

    def test_a_FAILED_acl_enumeration_is_ANNOUNCED_and_not_silent(self, monkeypatch, caplog):
        """⛔ The `except` arm is a fail-open that #13287 owns, and it is the
        WORSE of the two: `query_all` RAISES on an outage by contract, so this arm
        returns with `ids == []` and the caller still drops EVERY graph and
        cascades the naming rows. It cannot refuse (`e2131c6847`, best-effort), so the
        least it must do is SAY so — and it never reaches the `LOWER BOUND`
        warning below, so without a sentence of its own the total loss of every
        `tenant_<gid>` credential is completely silent.

        Mutant: restore the bare `"team ACL-user enumeration failed … : %s"`
        message — this reds on the missing clause.
        """
        from tortoise import hosted_api as ha

        class _Broken:
            def query_all(self, *a, **k):
                raise RuntimeError("postgrest 503 on the graphs walk")

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: _Broken())
        caplog.set_level(logging.WARNING, logger="tortoise.hosted_api")
        assert ha._drop_org_acl_users("o1") is None   # must not raise (`e2131c6847`)
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "NOT blocked" in text, text
        assert "tenant_*" in text and "13287" in text, text


    def test_an_unreadable_enumeration_REFUSES_rather_than_erasing_a_subset(self, monkeypatch):
        """⛔ A failure to ask is not an answer (review round 4, P1).

        Degrading to "the derived names only" would let the purge delete the org
        rows while every custom `org_{id}_{gid}` graph stays resident — and once
        `purge_org_control_plane` has run, those namespaces exist NOWHERE: no
        resolver derives them and the reaper refuses an unresolved graph, so the
        erasure is committed, incomplete and unrecoverable. Raising keeps the
        org row as the #926 retry anchor, and the next sweep completes it.
        """
        from tortoise import hosted_api as ha

        class _Broken:
            # ⛔ `query_all`, NOT `query`: `_org_graph_names` calls the walk now, so
            # a `query`-only double raised `AttributeError` ("no attribute
            # 'query_all'") and this test silently became a DUPLICATE of
            # ``test_a_seam_without_query_all_is_ANNOUNCED_not_silent`` — the
            # store-OUTAGE arm its own docstring names was left unpinned. That
            # matters because `query_all` "RAISES on an outage by contract" is the
            # load-bearing sentence both new docstrings lean on: a mutant that
            # swallowed only `RuntimeError` inside the walk kept the suite green
            # while re-opening the unrecoverable partial-erasure path.
            def query_all(self, *a, **k):
                raise RuntimeError("postgrest down")

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: _Broken())
        with pytest.raises(RuntimeError, match="refusing to deprovision a SUBSET") as exc:
            ha._org_graph_names("o1", "org_o1")
        assert "postgrest down" in str(exc.value), (
            "the refusal must carry the STORE's error — an AttributeError here "
            "would mean the double is dead again")

    def test_a_failed_enumeration_keeps_the_org_row_as_the_retry_anchor(self, monkeypatch):
        """The #926 anchor, end to end: the sweep logs and skips the org, so the
        row (and therefore the retry) survives an enumeration failure."""
        from tortoise import hosted_api as ha

        def _boom(org_id, graph_name=None):
            raise RuntimeError("graph enumeration failed for org o1: postgrest down")

        monkeypatch.setattr(ha, "_drop_org_graphs_strict", _boom)
        rows = [{"id": "o1", "graph_name": "org_o1", "deleted_at": "2000-01-01T00:00:00+00:00",
                 "grace_hours": 0}]
        purged: list[str] = []

        class _CP:
            def query(self, table, **kw):
                if table == "organizations":
                    return rows
                return []

        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: _CP())
        monkeypatch.setattr(ha, "_drop_org_acl_users", lambda oid: None)
        monkeypatch.setattr("tortoise.supabase_control.purge_org_control_plane",
                            lambda cp, oid: purged.append(oid))
        ha._purge_deleted_orgs()
        assert purged == [], (
            "the control-plane rows were deleted after a failed enumeration — "
            "the custom graphs are now unrecoverable")

    def test_the_whole_org_is_ACTUALLY_GONE_from_a_live_store(self, monkeypatch):
        """THE FALSIFIER for the ordering rule — a live store, the real seam.

        Every other test here monkeypatches the drop, so none of them can see the
        engine behaviour that makes ordering load-bearing: binding a graph the
        store does not hold MATERIALISES it, and a bind AFTER a ``GRAPH.DELETE``
        brings the graph back. Measured on ``falkordb-server:v4.20.6`` — with the
        list in the WRONG order this test's final assertion fails
        (``['org_<id>']`` still listed), which is exactly the silent un-erasure
        the PR review caught.

        ⛔ Read ``list_graphs()``, never ``select_graph(name)`` — the second is the
        very bind that resurrects the graph, so an assertion written that way
        would recreate what it is checking.
        """
        import os

        if not os.environ.get("TORTOISE_DB_URI"):
            pytest.skip("needs a live store (TORTOISE_DB_URI)")
        from tortoise import hosted_api as ha

        org = f"z13205probe{os.getpid():x}"
        canonical, legacy = f"org_{org}", f"team_{org}"
        db = ha._make_sdk(graph_name="tortoise")._get_proj().db
        for name in (canonical, legacy):
            db.select_graph(name).query("CREATE (:Probe13205 {i: 0})")
        try:
            assert canonical in db.list_graphs(), "seed failed"
            monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                                lambda: False)
            names = ha._drop_org_graphs_strict(org, canonical)
            assert names[-1] == canonical, names
            listed = set(db.list_graphs() or [])
            assert canonical not in listed, (
                "the org's data graph survived its own purge — the drop loop "
                "bound it again after deleting it")
            assert legacy not in listed, listed
        finally:
            for name in (canonical, legacy):
                with contextlib.suppress(Exception):
                    db.select_graph(name).delete()

    def test_strict_deprovision_drops_each_name_and_is_rerunnable(self, monkeypatch):
        """The strict seam the purge calls: one drop per owned name, and a second
        run (every graph already gone) is a success, not a failure — the #926
        retry anchor must converge."""
        from tortoise import hosted_api as ha

        dropped: list[str] = []
        monkeypatch.setattr(ha, "_org_graph_names",
                            lambda org_id, graph_name=None: ["team_o1", "org_o1"])
        monkeypatch.setattr(ha, "_drop_org_graph_strict",
                            lambda org_id, graph_name=None: dropped.append(graph_name))
        assert ha._drop_org_graphs_strict("o1", "org_o1") == ["team_o1", "org_o1"]
        assert dropped == ["team_o1", "org_o1"]
        dropped.clear()
        ha._drop_org_graphs_strict("o1", "org_o1")
        assert dropped == ["team_o1", "org_o1"]


# ── 2c. the SHARED double derives its certificate — pinned here, because nothing else did ──


class TestTheSharedDoubleDerivesItsCertificate:
    """``FakeControlPlane.query_all`` must DERIVE ``complete``, never assert it.

    ⛔ This is the one arm of the shared double that no test exercised: replacing
    ``_certifiable``'s body with ``return True`` reddened **nothing** — 566 tests
    passed across the six suites that use this fake — while the same edit on the
    test-local ``_CP`` reds three deprovision rows. A double that certifies
    unconditionally is the exact shape of wrongness this PR removes:
    ``_org_graph_names`` REFUSES an uncertified walk, so a fake that always
    certifies makes a walk production would refuse look green.

    The arms below are four edges of the real rule (``supabase_control``):
    ``advanceable = [c for c in cursors if c]; if not advanceable: break`` — a
    page with NO cursor cannot advance; a page with one can, and keeping its
    uncursored rows is what production does; an ordinary page certifies; and
    ``if any(a >= b for a, b in itertools.pairwise(advanceable)): break`` — a
    page that is NOT STRICTLY ASCENDING cannot certify either, because the server
    may have ignored ``order`` and a later cursor would step OVER rows that were
    never served. That fourth edge was missing until review round 17: the double
    certified a duplicated page while the store refuses it, which is the LOOSE
    direction and the one that matters to a purge.
    """

    @staticmethod
    def _cp(rows):
        from tests.fake_control_plane import FakeControlPlane
        return FakeControlPlane({"graphs": rows})

    def test_a_page_with_NO_cursor_value_anywhere_is_REFUSED_the_certificate(self):
        cp = self._cp([{"namespace": "org_1"}, {"namespace": "org_2"}])
        rows, complete = cp.query_all(
            "graphs", select=["id", "namespace"], cursor="id")
        assert len(rows) == 2, "the rows are still RETURNED — only the certificate is withheld"
        assert complete is False, (
            "the shared double CERTIFIED a page carrying no cursor value at "
            "all: production's walk cannot advance from it (`advanceable` is "
            "empty), so `_org_graph_names` refuses. This certificate must be "
            "derived from the rows, not asserted")

    def test_a_page_with_AT_LEAST_ONE_cursor_value_still_advances_and_certifies(self):
        cp = self._cp([{"id": "g1", "namespace": "org_1"},
                       {"namespace": "org_2"}])
        rows, complete = cp.query_all(
            "graphs", select=["id", "namespace"], cursor="id")
        assert len(rows) == 2, "production KEEPS the uncursored rows it saw"
        assert complete is True, (
            "a MIXED page is not refused by production — it advances from its "
            "cursored rows. Certifying only wholly-cursored pages would be "
            "STRICTER than the real store and would refuse walks it completes")

    def test_an_ordinary_walk_is_certified(self):
        cp = self._cp([{"id": "g1", "namespace": "org_1"},
                       {"id": "g2", "namespace": "org_2"}])
        rows, complete = cp.query_all(
            "graphs", select=["id", "namespace"], cursor="id")
        assert len(rows) == 2
        assert complete is True, (
            "the derivation must certify an ordinary walk — otherwise the "
            "refusal arms would be satisfied by a double that refuses everything")

    def test_a_page_that_is_NOT_STRICTLY_ASCENDING_is_REFUSED_the_certificate(self):
        """⛔ Production refuses a page that is not STRICTLY ascending on the
        cursor: ``if any(a >= b for a, b in itertools.pairwise(advanceable)):
        break``. A server that ignored ``order`` would have the walk step OVER
        rows that were never served and STILL end on an empty page, reporting a
        COMPLETE walk over a subset. Omitting the gate is the LOOSE direction —
        the double certifies what the store refuses — so it is pinned on the
        predicate itself.

        ⛔ THE PREDICATE, NOT ``query_all``, ON PURPOSE: ``query_all`` reads
        through ``query_with_total(order=cursor)``, and this fake HONOURS
        ``order`` by sorting — so a descending fixture arrives ascending and the
        shape can never reach ``_certifiable`` that way. The duplicate stays
        reachable through ``query_all`` and is asserted that way too.

        Mutant: delete the ``pairwise`` check from ``_certifiable`` — this reds
        while every other arm in this class stays green.
        """
        from tests.fake_control_plane import FakeControlPlane

        certifiable = FakeControlPlane._certifiable
        assert certifiable([{"id": "g1"}, {"id": "g1"}], None, "id") is False, (
            "a DUPLICATE cursor must not certify — the real walk breaks on "
            "`a >= b` and never certifies")
        assert certifiable([{"id": "g2"}, {"id": "g1"}], None, "id") is False, (
            "a DESCENDING page must not certify, for the same reason")
        assert certifiable([{"id": "g1"}, {"id": "g2"}], None, "id") is True, (
            "…while a strictly ascending page still does, so the gate cannot be "
            "satisfied by refusing everything")

    def test_a_DUPLICATE_cursor_is_REFUSED_through_query_all_too(self):
        """The reachable shape of the same gate: a duplicated page."""
        cp = self._cp([{"id": "g1", "namespace": "org_1"},
                       {"id": "g1", "namespace": "org_2"}])
        _rows, complete = cp.query_all(
            "graphs", select=["id", "namespace"], cursor="id")
        assert complete is False, (
            "the double certified a page with a REPEATED cursor; the real walk "
            "breaks on `a >= b`, so `_org_graph_names` would refuse the purge "
            "while this double called it green")

    def test_the_STATED_TOTAL_gate_counts_DISTINCT_CURSOR_VALUES(self):
        """Gate 3 of the real certificate, which nothing exercised.

        Production ends with ``complete = exhausted and (total is None or
        len(seen) >= total)``, and ``seen`` is a SET OF CURSOR VALUES — not rows.
        Measured: replacing the gate with ``return True`` left 554 tests green
        across the six suites that use this fake, because the only call that
        supplies a stated total in the whole corpus does so in a direction the
        gate cannot refuse. That is the same "false PASS in the double that
        decides a purge" class the earlier rounds fixed on the other two gates,
        so all three rows are pinned here — unmet total, the distinct-cursor rule
        (a DUPLICATE cannot satisfy a total of 2), and the met-total control that
        stops the gate being satisfied by refusing everything.
        """
        from tests.fake_control_plane import FakeControlPlane

        certifiable = FakeControlPlane._certifiable
        assert certifiable([{"id": "g1"}], 2, "id") is False, (
            "one row against a stated total of 2 must not certify — the walk did "
            "not meet the total the server stated")
        assert certifiable([{"id": "g1"}, {"id": "g1"}], 2, "id") is False, (
            "the total counts DISTINCT CURSOR VALUES, not rows: two rows carrying "
            "the same gid are one seen row, so a total of 2 is not met")
        assert certifiable([{"id": "g1"}, {"id": "g2"}], 2, "id") is True, (
            "…and a met total still certifies, so the gate cannot be satisfied by "
            "refusing everything")

    def test_an_EMPTY_page_is_the_end_of_the_walk_and_CERTIFIES(self):
        cp = self._cp([])
        rows, complete = cp.query_all(
            "graphs", select=["id", "namespace"], cursor="id")
        assert rows == []
        assert complete is True, (
            "an EMPTY page is the sound end-of-walk signal (production: `if not "
            "page: exhausted = True; break`), so it CERTIFIES. Refusing it is "
            "STRICTER than the store, and `_org_graph_names` RAISES on an "
            "uncertified walk — so an org owning no custom graphs could never be "
            "purged at all")


# ── 3. the reaper: safe by construction ──────────────────────────────────────

def _org(org_id, name="acme", email=None):
    return {"id": org_id, "name": name, "email": email, "graph_name": f"org_{org_id}"}


class _FakeCP:
    """A control plane that answers by table, so the REAL index runs.

    It also HONOURS ``select`` on both read paths, but only the ``query_all`` half
    is LOAD-BEARING: that is where the ``graphs`` walk happens, and a fake that
    returned whole rows whatever columns were asked for could not tell a read that
    forgot ``status``/``kind`` from one that did not. ``query`` serves the
    ``organizations`` read, which never asks for those columns — it is projected
    for fidelity, and reverting it leaves the suite green.

    ``graphs_complete=False`` models a walk the server could not certify (a
    deployment `max_rows` cap), which is now a REFUSAL rather than a shorter
    index.
    """

    def __init__(self, *, orgs, graphs, graphs_complete=True, graphs_raise=None):
        self._orgs = orgs
        self._graphs = graphs
        self._complete = graphs_complete
        self._raise = graphs_raise

    def query(self, table, **kw):
        rows = self._orgs if table == "organizations" else self._graphs
        select = kw.get("select")
        if not select:
            return rows
        return [{k: r[k] for k in select if k in r} for r in rows]

    def query_all(self, table, **kw):
        if self._raise is not None:
            raise self._raise
        rows = self._orgs if table == "organizations" else self._graphs
        select = kw.get("select")
        if select:
            rows = [{k: r[k] for k in select if k in r} for r in rows]
        return rows, self._complete


def _owned(org, gid=None):
    """One ``_namespace_index`` entry: the owning row plus a custom graph's gid."""
    return {"org": org, "gid": gid}


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
                name, org_index={"registry_tortoise": _owned(_org("x", "agent-x"))},
                node_count=0)
            assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, name

    def test_an_unresolvable_name_is_never_assumed_to_be_test(self):
        """Order matters: ownership is decided BEFORE the test marker, so an
        unresolvable graph never gets filed as "some org's, not test" — which
        would read like a decision about an owner that does not exist."""
        bucket, _ = reaper.classify_graph(
            "org_nobody", org_index={"org_someone": _owned(_org("o", "agent-x"))},
            node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED

    def test_an_unreadable_emptiness_read_is_a_refusal(self):
        bucket, reason = reaper.classify_graph(
            "org_o1", org_index={"org_o1": _owned(_org("o1", "agent-signup"))},
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
                f"org_{org['id']}", org_index={f"org_{org['id']}": _owned(org)},
                node_count=0)
            assert bucket == reaper.BUCKET_KEPT_NOT_TEST_ORG, org

    def test_a_non_empty_test_graph_is_kept(self):
        """Data is a data question. ``Apresto Internal Test`` held 47 MB of real
        content — an empty-only gate is what keeps a cleaner from being an
        incident."""
        bucket, reason = reaper.classify_graph(
            "org_o1", org_index={"org_o1": _owned(_org("o1", "agent-x"))}, node_count=3)
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

    def test_a_foreign_graphs_row_cannot_borrow_a_test_org(self):
        """⛔ THE FAIL-OPEN THIS TOOL MUST NOT HAVE (#13205 review).

        `public.graphs.namespace` is trusted verbatim in the naive index, so a
        malformed or hostile row ``{org_id: <test org>, namespace: "org_<a
        customer>_g_x"}`` would attribute the CUSTOMER's graph to a test org —
        and an empty customer graph would be deleted. Ownership is decided by
        `_owns_namespace`, not by the row.

        The row carries `status: active` because `graphs.status` is `NOT NULL`: a
        row with NO status is a shape production cannot produce (a tombstone is
        still a status — `'deleted'`). Ownership is consulted BEFORE the status
        gate, so this fixture's status is inert either way; it is here for shape
        fidelity, not to pin a gate."""
        cp = _FakeCP(
            orgs=[_org("agent-1", "agent-signup"), _org("cust-1", "Daylight")],
            graphs=[{"id": "g_x", "org_id": "agent-1",
                     "namespace": "org_cust-1_g_x", "status": "active"}])
        index = reaper._namespace_index(cp)
        assert "org_cust-1_g_x" not in index, index
        bucket, _ = reaper.classify_graph(
            "org_cust-1_g_x", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED

    def test_a_graph_name_claimed_by_two_orgs_is_refused_not_resolved_by_order(self):
        """`setdefault` over an unordered query made the keep/reap decision
        depend on PostgREST's row order. Ambiguity is a refusal, never a race."""
        cp = _FakeCP(
            orgs=[{"id": "o1", "name": "agent-one", "email": None,
                   "graph_name": "org_shared"},
                  {"id": "o2", "name": "Daylight", "email": None,
                   "graph_name": "org_shared"}],
            graphs=[])
        index = reaper._namespace_index(cp)
        assert "org_shared" not in index, index
        bucket, _ = reaper.classify_graph(
            "org_shared", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED

    def test_a_custom_graph_row_carries_its_gid_for_the_acl_drop(self):
        """A custom graph's per-graph ACL user is GLOBAL state keyed on the gid;
        reaping the graph without it leaves a live credential behind (#2113 C4).
        The report therefore has to carry the gid to `reap`."""
        cp = _FakeCP(
            orgs=[_org("agent-1", "agent-signup")],
            graphs=[{"id": "g_abc", "org_id": "agent-1",
                     "namespace": "org_agent-1_g_abc", "status": "active"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1_g_abc"]["gid"] == "g_abc"
        assert index["org_agent-1"]["gid"] is None

    def test_an_unreadable_graphs_read_is_a_refusal(self):
        """⛔ The `graphs` read is the ONLY source of gids, so degrading to
        "default graphs only" would let a PROMOTED org's default (its
        `graph_name` IS a `org_{id}_{gid}` namespace) resolve from the
        organizations row with `gid: None`, classify as reapable, and be deleted
        with its global `tenant_<gid>` credential left live (round-4 review)."""
        cp = _FakeCP(orgs=[_org("agent-1", "agent-signup")], graphs=[],
                     graphs_raise=RuntimeError("PGRST: statement timeout"))
        with pytest.raises(reaper.Refusal, match="graphs read failed"):
            reaper._namespace_index(cp)

    def test_an_uncertifiable_graphs_walk_is_a_refusal(self):
        """⛔ A single-page `query` is silently capped at the deployment's
        `max_rows` (1000) and never says so, so a promoted org's `graphs` row
        could simply be MISSING from the page while its `organizations.
        graph_name` still names that custom namespace. The walk is therefore
        `query_all`, and an uncertified one is a refusal (round-5 review)."""
        cp = _FakeCP(orgs=[_org("agent-1", "agent-signup")], graphs=[],
                     graphs_complete=False)
        with pytest.raises(reaper.Refusal, match="could not be certified"):
            reaper._namespace_index(cp)

    def test_a_tombstoned_DEFAULT_row_is_still_reclaimable(self):
        """⛔ A `kind='default'` tombstone must not be REFUSED: no per-graph ACL user
        is minted for a default, `restore_graph` refuses the kind, and the purge
        sweep enumerates CUSTOM tombstones only — so refusing it strands an empty
        leaked graph for ever (#13262's neighbour). It is reaped, and if a graph
        demoted from custom still has `tenant_<id>` that user is dropped first.
        """
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_old", "org_id": "agent-1", "kind": "default",
                     "namespace": "org_agent-1", "status": "deleted"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1"]["gid"] == "g_old", index
        bucket, reason = reaper.classify_graph(
            "org_agent-1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REAPABLE, reason

    def test_a_drift_default_tombstone_cannot_lend_its_id_to_a_custom_namespace(self):
        """⛔ The deferred pass lends a gid too, so it makes the SAME agreement check
        the live pass does. A non-live `kind='default'` row on a CUSTOM-shaped
        namespace (`id` != the embedded gid) must not hand `reap()` its own id —
        that would DELUSER `tenant_<id>` and delete the graph whose real credential
        `tenant_{gid}` stays live (measured: a real fail-open before this check).
        """
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_def", "org_id": "agent-1", "kind": "default",
                     "namespace": "org_agent-1_g_abc", "status": "deleted"}])
        index = reaper._namespace_index(cp)
        bucket, reason = reaper.classify_graph(
            "org_agent-1_g_abc", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, (index, bucket)
        assert "disagrees" in reason, reason

    def test_a_coexisting_default_tombstone_does_not_reap_a_custom_tombstone(self):
        """A `kind='custom'` tombstone is refused even when a `kind='default'`
        tombstone shares its namespace, in BOTH drift directions: a DEFERRED gid
        came from a non-live row, so it may not be treated as the live verdict that
        suppresses a refusal — otherwise a restorable custom graph is destroyed."""
        for default_id, custom_id in (("g_def", "g_abc"), ("g_abc", "g_def")):
            cp = _FakeCP(
                orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                       "graph_name": "org_agent-1"}],
                graphs=[{"id": custom_id, "org_id": "agent-1", "kind": "custom",
                         "namespace": "org_agent-1_g_abc", "status": "deleted"},
                        {"id": default_id, "org_id": "agent-1", "kind": "default",
                         "namespace": "org_agent-1_g_abc", "status": "deleted"}])
            for reversed_rows in (cp, _FakeCP(orgs=cp._orgs,
                                             graphs=list(reversed(cp._graphs)))):
                index = reaper._namespace_index(reversed_rows)
                bucket, _ = reaper.classify_graph(
                    "org_agent-1_g_abc", org_index=index, node_count=0)
                assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, (
                    default_id, custom_id, index, bucket)

    def test_two_non_live_default_rows_are_not_resolved_by_row_order(self):
        """Two NON-LIVE default rows with different ids share one bare namespace in
        a drift estate; whichever id is dropped decides whose `tenant_<id>` dies, so
        the name is refused as ambiguous rather than resolved by query order."""
        for rows in ([{"id": "g_a", "org_id": "agent-1", "kind": "default",
                       "namespace": "org_agent-1", "status": "deleted"},
                      {"id": "g_b", "org_id": "agent-1", "kind": "default",
                       "namespace": "org_agent-1", "status": "deleted"}],
                     [{"id": "g_b", "org_id": "agent-1", "kind": "default",
                       "namespace": "org_agent-1", "status": "deleted"},
                      {"id": "g_a", "org_id": "agent-1", "kind": "default",
                       "namespace": "org_agent-1", "status": "deleted"}]):
            cp = _FakeCP(
                orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                       "graph_name": "org_agent-1"}], graphs=rows)
            index = reaper._namespace_index(cp)
            bucket, _ = reaper.classify_graph(
                "org_agent-1", org_index=index, node_count=0)
            assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, (rows, index)

    def test_a_custom_shaped_graph_with_no_gid_is_refused(self):
        """⛔ THE GATE MUST FIRE ON INPUT THE REAL INDEX PRODUCES (round-5 review).

        The round-4 version exempted the org's stored `graph_name`, so a PROMOTED
        default — whose `graph_name` IS `org_{id}_{gid}` — was exempted and the
        gate was unreachable for exactly the graph it exists to protect. This
        drives the REAL `_namespace_index`: the org row names the custom
        namespace, and the `graphs` row for it is absent, so the entry carries
        `gid: None`.
        """
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1_g_9"}],
            graphs=[])  # the row is MISSING (a short/unfiltered page)
        index = reaper._namespace_index(cp)
        assert index["org_agent-1_g_9"]["gid"] is None
        bucket, reason = reaper.classify_graph(
            "org_agent-1_g_9", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, index
        assert "ACL user" in reason
        org = index["org_agent-1_g_9"]["org"]
        assert reaper._looks_custom("org_agent-1_g_9", org) is True
        # the BARE defaults need no gid and stay reapable
        assert reaper._looks_custom("org_agent-1", org) is False
        assert reaper._looks_custom("team_agent-1", org) is False

    def test_a_promoted_default_keeps_its_gid(self):
        """⛔ The gid must survive a DOUBLE claim by the SAME org (round-3 review).

        A promoted (or repaired) org's ``organizations.graph_name`` IS a custom
        ``org_{id}_{gid}`` namespace, so the namespace is claimed twice: once by
        the org row (no gid) and once by its ``graphs`` row (with the gid).
        `setdefault` kept the first claim, so the report carried ``gid: None``,
        `reap()` skipped the ACL drop — and the global ``tenant_<gid>`` credential
        outlived the graph, which is precisely what the ACL gate exists to stop.
        """
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1_g_9"}],
            graphs=[{"id": "g_9", "org_id": "agent-1",
                     "namespace": "org_agent-1_g_9", "status": "active"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1_g_9"]["gid"] == "g_9", index
        assert index["org_agent-1_g_9"]["org"]["id"] == "agent-1"

    def test_a_tombstoned_default_cannot_lend_its_gid(self):
        """⛔ The gid must come from the LIVE row, never from row order (round-4).

        `uq_graphs_org_default_active` is
        ``UNIQUE (org_id) WHERE kind='default' AND status <> 'deleted'``, so a
        tombstoned default row coexists with the live one and BOTH carry the same
        bare namespace. A default graph has no MINTED per-graph ACL user, so the
        harm here is not a wrong credential — it is that which row wins was decided
        by row ORDER, and the same estate could resolve differently run to run.
        The fix makes it deterministic: the LIVE row always speaks for the
        namespace, and the graph stays reapable.
        """
        tombstone = {"id": "g_old", "org_id": "agent-1",
                     "namespace": "org_agent-1", "status": "deleted"}
        live = {"id": "g_new", "org_id": "agent-1",
                "namespace": "org_agent-1", "status": "active"}
        orgs = [{"id": "agent-1", "name": "agent-signup", "email": None,
                 "graph_name": "org_agent-1"}]
        for order in ([tombstone, live], [live, tombstone]):
            index = reaper._namespace_index(_FakeCP(orgs=orgs, graphs=order))
            assert index["org_agent-1"]["gid"] == "g_new", (order, index)
            # A live row DID attribute the credential, so the tombstone's presence
            # must not turn the graph into a refusal with a reason claiming no gid
            # could be attributed (round-6 review).
            bucket, reason = reaper.classify_graph(
                "org_agent-1", org_index=index, node_count=0)
            assert bucket == reaper.BUCKET_REAPABLE, (order, reason)
            assert "gid" not in reason or "no gid" not in reason, reason

    def test_a_sibling_name_under_an_org_prefix_gets_no_gid(self):
        """`org_{org_id}_backup` matches the org's own prefix but embeds no gid, so
        nothing may INVENT one for it — and because it reads as a per-graph
        namespace, `classify_graph`'s independent gate refuses it rather than
        reaping a graph whose credential nobody can name. Both halves matter: a
        derived gid here would be a DELUSER of a credential that never existed.
        """
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1_backup"}],
            graphs=[])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1_backup"]["gid"] is None, index
        bucket, reason = reaper.classify_graph(
            "org_agent-1_backup", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, bucket
        assert "cannot be dropped" in reason, reason

    def test_a_g_less_tail_is_not_mistaken_for_a_per_graph_namespace(self):
        """The `g_` SHAPE is load-bearing in the other direction too: a live row on
        `org_{id}_backup` must not be REFUSED as a per-graph namespace whose id
        disagrees — its tail is not a gid, so no per-graph credential is claimed
        and nothing is compared against one (round-6 review)."""
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1_backup"}],
            graphs=[{"id": "g_row", "org_id": "agent-1",
                     "namespace": "org_agent-1_backup", "status": "active"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1_backup"]["gid"] == "g_row", index

    def test_two_live_gids_for_one_namespace_are_refused(self):
        """Two LIVE credentials on one namespace: dropping either would be a
        guess, so the namespace leaves the index and the graph reads as
        unresolved (never reaped, never DELUSERed)."""
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_one", "org_id": "agent-1",
                     "namespace": "org_agent-1", "status": "active"},
                    {"id": "g_two", "org_id": "agent-1",
                     "namespace": "org_agent-1", "status": "active"}])
        index = reaper._namespace_index(cp)
        assert "org_agent-1" not in index, index
        bucket, _ = reaper.classify_graph(
            "org_agent-1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED

    def test_a_row_without_a_status_is_not_live(self):
        """`graphs.status` is `text NOT NULL DEFAULT 'active'` and the read always
        selects it, so a row that arrives WITHOUT one came from a control plane we
        cannot vouch for. Unknown liveness must never lend the gid that decides a
        DELUSER, and a namespace whose only rows are unknown is refused rather than
        reaped gid-less (round-5 review)."""
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_old", "org_id": "agent-1",
                     "namespace": "org_agent-1"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1"]["gid"] is None, index
        bucket, reason = reaper.classify_graph(
            "org_agent-1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, reason

    def test_a_gid_less_claim_with_only_tombstoned_rows_is_refused(self):
        """A namespace whose only rows are non-live is refused WITH its reason, and
        never reaped gid-less. These rows carry no `kind`, so they are read as the
        conservative case (custom): unknown liveness must not authorise a deletion
        of a graph that may still be restorable.
        """
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_old", "org_id": "agent-1",
                     "namespace": "org_agent-1", "status": "deleted"}])
        index = reaper._namespace_index(cp)
        bucket, reason = reaper.classify_graph(
            "org_agent-1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, bucket
        assert "not live" in reason and "gid" in reason, reason

    def test_an_unrecognised_status_is_not_live(self):
        """`graphs.status` is FREE TEXT (no CHECK), so the gate must be
        "anything but 'active'", not a vocabulary of the two values we know:
        narrowing it to `status == 'deleted'` would re-open the gid lend for
        'archived'/'pending' rows (round-5 review)."""
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_one", "org_id": "agent-1",
                     "namespace": "org_agent-1", "status": "archived"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1"]["gid"] is None, index
        bucket, _ = reaper.classify_graph(
            "org_agent-1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED

    def test_a_tombstoned_custom_graph_is_never_reaped(self):
        """A trashed graph's namespace is REFUSED (with a reason), not reaped and
        not silently unowned: its gid is nameable off the namespace, but a tombstone
        is restorable within grace, so destroying it is the purge sweep's call — and
        the report must say that instead of "no organizations row owns this graph
        name", which would send an operator hunting a nonexistent org.
        """
        cp = _FakeCP(
            orgs=[_org("agent-1", "agent-signup")],
            graphs=[{"id": "g_gone", "org_id": "agent-1",
                     "namespace": "org_agent-1_g_gone", "status": "deleted"}])
        index = reaper._namespace_index(cp)
        bucket, reason = reaper.classify_graph(
            "org_agent-1_g_gone", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, bucket
        assert "TOMBSTONE" in reason and "purge" in reason, reason

    def test_a_live_custom_row_without_an_id_is_refused_not_reconstructed(self):
        """A live CUSTOM row with no primary key must NOT have its gid
        reconstructed from the namespace name: a missing gid never authorises a
        deletion, and here the gid IS the credential (`tenant_{gid}`). Refusing is
        the only safe answer (round-6 review)."""
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": None, "org_id": "agent-1",
                     "namespace": "org_agent-1_g_1", "status": "active"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1_g_1"]["gid"] is None, index
        bucket, reason = reaper.classify_graph(
            "org_agent-1_g_1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, bucket
        assert "carried no id" in reason, reason

    def test_the_two_refusal_messages_are_chosen_by_namespace_shape(self):
        """The refusal message must match the situation: a namespace that names NO
        gid reports that no credential can be attributed, while a per-graph
        namespace reports that the credential IS spelled by the name and the
        refusal is about destroying the graph. Both are reachable (round-6)."""
        bare = reaper._namespace_index(_FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_old", "org_id": "agent-1",
                     "namespace": "org_agent-1", "status": "deleted"}]))
        _, bare_reason = reaper.classify_graph(
            "org_agent-1", org_index=bare, node_count=0)
        assert "no gid can be attributed" in bare_reason, bare_reason

        custom = reaper._namespace_index(_FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_1", "org_id": "agent-1",
                     "namespace": "org_agent-1_g_1", "status": "deleted"}]))
        _, custom_reason = reaper.classify_graph(
            "org_agent-1_g_1", org_index=custom, node_count=0)
        assert "does spell the credential" in custom_reason, custom_reason
        assert "no gid can be attributed" not in custom_reason, custom_reason

    def test_a_row_whose_id_disagrees_with_its_namespace_gid_is_refused(self):
        """⛔ A malformed (or hostile) row must never lend an arbitrary gid.

        The namespace is the authority on which credential belongs to the graph; a
        row claiming `id = g_victim` for the namespace `org_agent-1_g_1` would make
        `reap()` DELUSER `tenant_g_victim` and leave `tenant_g_1` behind — a
        wrong-credential drop on the tool's most dangerous axis (round-5 review).
        """
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": "g_victim", "org_id": "agent-1",
                     "namespace": "org_agent-1_g_1", "status": "active"}])
        index = reaper._namespace_index(cp)
        bucket, reason = reaper.classify_graph(
            "org_agent-1_g_1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED, bucket
        assert "disagrees" in reason, reason

    def test_a_registry_lane_graph_name_is_not_over_refused(self):
        """`organizations.graph_name` is authoritative VERBATIM, and for an SDK-lane
        org it is `org_{name}` with name != id (#2023). That namespace embeds no
        gid, so it must stay a normal gid-less default graph — not be refused with
        a reason about a gid it never had (round-5 review)."""
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_acme"}],
            graphs=[])
        index = reaper._namespace_index(cp)
        assert index["org_acme"]["gid"] is None, index
        bucket, _ = reaper.classify_graph(
            "org_acme", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REAPABLE, bucket

    def test_a_live_row_without_an_id_is_refused(self):
        """The gid decides a DELUSER, so an unreadable one is a refusal — the
        same ruling the status gate makes, applied to the other read that can come
        back empty (round-5 review)."""
        cp = _FakeCP(
            orgs=[{"id": "agent-1", "name": "agent-signup", "email": None,
                   "graph_name": "org_agent-1"}],
            graphs=[{"id": None, "org_id": "agent-1",
                     "namespace": "org_agent-1", "status": "active"}])
        index = reaper._namespace_index(cp)
        assert index["org_agent-1"]["gid"] is None, index
        bucket, _ = reaper.classify_graph(
            "org_agent-1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_REFUSED_UNRESOLVED

    def test_a_customer_graph_is_judged_by_its_owner_not_its_credential(self):
        """A customer org is ruled out by the test marker, so its gid-less claim
        must read `kept-not-test-org` — not as an unattributable orphan. The
        credential verdict is moot for a graph this tool would never touch."""
        cp = _FakeCP(
            orgs=[{"id": "cust-1", "name": "Daylight Computer Co",
                   "email": None, "graph_name": "org_cust-1"}],
            graphs=[{"id": "g_old", "org_id": "cust-1",
                     "namespace": "org_cust-1", "status": "deleted"}])
        index = reaper._namespace_index(cp)
        bucket, _ = reaper.classify_graph(
            "org_cust-1", org_index=index, node_count=0)
        assert bucket == reaper.BUCKET_KEPT_NOT_TEST_ORG, index

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
        "org_o1": _owned(_org("o1", "agent-signup")),
        "org_customer": _owned(_org("c1", "Daylight Computer Co")),
    }
    rows = []
    for name in proj.db.list_graphs():
        needs = name in org_index
        count = 0 if needs else None
        bucket, reason = reaper.classify_graph(
            name, org_index=org_index, node_count=count)
        entry = org_index.get(name) or {}
        rows.append({"graph": name, "bucket": bucket, "reason": reason,
                     "org_id": (entry.get("org") or {}).get("id"),
                     "org_name": (entry.get("org") or {}).get("name"),
                     "gid": entry.get("gid"), "node_count": count})
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

    def test_a_custom_graph_drops_its_acl_user_before_the_graph(self, monkeypatch):
        """Order, and the reason for it: the ACL user is the live credential that
        outlives the graph. The drop is VERIFIED via `acl_user_exists`, not
        trusted — both `drop_acl_user` and `_acl_user_drop_hook` are best-effort
        and swallow, so a refused `ACL DELUSER` reads as success from the caller
        (measured). The real module is patched, not the swallowing hook."""
        order: list[str] = []
        monkeypatch.setattr("tortoise.acl_graph_users.drop_acl_user",
                            lambda gid: order.append(f"acl:{gid}"))
        monkeypatch.setattr("tortoise.acl_graph_users.acl_user_exists",
                            lambda gid: False)

        class _Graph:
            def __init__(self, name):
                self.name = name

            def delete(self):
                order.append(f"graph:{self.name}")

        class _Proj:
            db = type("D", (), {"select_graph": lambda self, n: _Graph(n)})()

        report = {"graphs": [
            {"graph": "org_a_g_1", "bucket": reaper.BUCKET_REAPABLE,
             "org_id": "a", "gid": "g_1"},
            {"graph": "org_a", "bucket": reaper.BUCKET_REAPABLE,
             "org_id": "a", "gid": None},
        ], "counts": {}, "total": 2}
        outcome = reaper.reap(report, proj=_Proj())
        assert order == ["acl:g_1", "graph:org_a_g_1", "graph:org_a"], order
        assert all(o["deleted"] for o in outcome)

    def test_an_acl_user_that_SURVIVES_its_drop_blocks_the_deletion(self, monkeypatch):
        """⛔ The round-1 arm could never fire: it wrapped
        `hosted_api._acl_user_drop_hook`, which swallows `ImportError` and every
        exception, so a refused `ACL DELUSER` still deleted the graph and
        reported `error: None`. The gate is now the POSITIVE check — the user is
        still there after the drop, so the graph is NOT deleted and the failure
        is recorded. Measured: patching the hook to raise made the old test pass
        over an arm production cannot reach."""
        deleted: list[str] = []
        monkeypatch.setattr("tortoise.acl_graph_users.drop_acl_user",
                            lambda gid: None)  # "succeeds", silently
        monkeypatch.setattr("tortoise.acl_graph_users.acl_user_exists",
                            lambda gid: True)  # ... but the user is still live

        class _Proj:
            db = type("D", (), {
                "select_graph": lambda self, n: type(
                    "G", (), {"delete": lambda s: deleted.append(n)})()})()

        report = {"graphs": [
            {"graph": "org_a_g_1", "bucket": reaper.BUCKET_REAPABLE,
             "org_id": "a", "gid": "g_1"}], "counts": {}, "total": 1}
        outcome = reaper.reap(report, proj=_Proj())
        assert deleted == [], "the graph was deleted with its ACL user still live"
        assert outcome[0]["deleted"] is False
        assert "refusing" in outcome[0]["error"]

    def test_a_failed_acl_drop_does_not_orphan_a_live_credential(self, monkeypatch):
        deleted: list[str] = []

        def _boom(gid):
            raise RuntimeError("ACL DELUSER refused")

        monkeypatch.setattr("tortoise.acl_graph_users.drop_acl_user", _boom)
        monkeypatch.setattr("tortoise.acl_graph_users.acl_user_exists",
                            lambda gid: False)

        class _Proj:
            db = type("D", (), {
                "select_graph": lambda self, n: type(
                    "G", (), {"delete": lambda s: deleted.append(n)})()})()

        report = {"graphs": [
            {"graph": "org_a_g_1", "bucket": reaper.BUCKET_REAPABLE,
             "org_id": "a", "gid": "g_1"}], "counts": {}, "total": 1}
        outcome = reaper.reap(report, proj=_Proj())
        assert deleted == [], "the graph was deleted with its ACL user still live"
        assert outcome[0]["deleted"] is False
        assert "acl-user drop failed" in outcome[0]["error"]

    def test_json_stdout_is_one_parseable_document(self, monkeypatch, capsys):
        """`--json` is documented as machine-readable. The human notice used to
        be appended to the same stdout, so `| jq` got a parse error — and
        `--execute --json` printed the report document AND the outcome document,
        i.e. two documents, which `json.loads` rejects as "Extra data"."""
        import json as _json

        monkeypatch.setattr(reaper, "build_report", lambda **kw: _real_report(
            type("P", (), {"db": type("D", (), {
                "list_graphs": lambda self: [
                    "org_o1", "org_customer", "orphan"]})()})()))
        rc = reaper.main(["--json"])
        out = capsys.readouterr().out
        doc = _json.loads(out)  # the whole stdout is ONE document
        assert doc["total"] == 3
        assert rc == 0

    def test_execute_json_emits_exactly_one_document(self, monkeypatch, capsys):
        """The `--execute --json` shape: exactly one JSON document on stdout, and
        the deletion must go through the write-free handle (never `_make_sdk`,
        which would MINT the reserved `tortoise` graph on the delete path)."""
        import json as _json

        deleted: list[str] = []

        class _Graph:
            def __init__(self, name):
                self.name = name

            def delete(self):
                deleted.append(self.name)

        class _Proj:
            db = type("D", (), {"select_graph": lambda self, n: _Graph(n)})()

        monkeypatch.setattr(reaper, "build_report", lambda **kw: _real_report(
            type("P", (), {"db": type("D", (), {
                "list_graphs": lambda self: [
                    "org_o1", "org_customer", "orphan"]})()})()))
        monkeypatch.setattr(reaper, "_read_handle", lambda: _Proj())
        monkeypatch.setattr("tortoise.hosted_api._make_sdk",
                            lambda *a, **k: pytest.fail(
                                "the delete path must not construct an SDK — it "
                                "would mint the reserved `tortoise` graph"))
        rc = reaper.main(["--json", "--execute"])
        out = capsys.readouterr().out
        doc = _json.loads(out)  # exactly ONE document, or this raises
        assert doc["failed"] == 0
        assert [o["graph"] for o in doc["outcome"]] == ["org_o1"]
        assert deleted == ["org_o1"]
        assert rc == 0
