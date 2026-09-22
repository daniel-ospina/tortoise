"""#3390: write-ahead mint journaling — the ownership line precedes the CREATE.

Orphan window (pre-fix): every product mint site materialized the graph and
journaled its ownership *afterwards*::

    graph.query(_init_q)              # effect
    _journal_append_product(name)     # ownership record

A kill in that gap left a graph no journal-driven sweep could see — an orphan.
The ownership journal is the record the journal-driven own/stale sweep
(`_sweep_drop`) reads, so the orphan leaks, makes the owned-set a rebuild
produces diverge from live (live != rebuild), and is reclaimable only by the
opt-in `_sweep_team_strays` pass. (`wipe_server` is fail-closed to
`test_`/`tortoise_test` prefixes and never touches `team_*`.)

These tests are HERMETIC — no DB, no lane. `org_create` and
`_eager_provision_org_graph` are driven against fake projections/registries that
record the exact call order; the product-level journal consumers
(`_created_since_last_wipe`, `_sweep_drop`) run over a real tmp FILE journal; and
an AST guard pins the write-ahead ordering at every mint site in
`tortoise/sdk.py`, `tortoise/hosted_api.py` and `tortoise/__main__.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent

# Every product function that mints a `:TeamMeta` graph — the guard fails
# loudly if this set changes, so a new site must adopt the seam + register here.
_MINT_SITES = {
    ("tortoise/sdk.py", "org_create"),
    ("tortoise/hosted_api.py", "provision_tenant"),
    ("tortoise/hosted_api.py", "register_user"),
    ("tortoise/hosted_api.py", "_eager_provision_org_graph"),
    ("tortoise/__main__.py", "_cmd_key_create"),
}

_GUARDED_FILES = ("tortoise/sdk.py", "tortoise/hosted_api.py", "tortoise/__main__.py")
_WRITE_AHEAD_SEAM = "journal_mint_write_ahead"


class _ProcessKilled(BaseException):
    """A mid-window PROCESS KILL: ``BaseException`` bypasses the mint sites'
    ``except Exception`` rollback handlers, so no recovery code runs — exactly
    the crash the write-ahead ordering must survive."""


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def journal(self, name: str, *_a, **_k) -> None:
        self.events.append(("journal", name))

    def index(self, needle) -> int:
        return self.events.index(needle)

    def names(self, kind: str) -> set[str]:
        return {e[1] for e in self.events if e[0] == kind}


class _FakeGraph:
    """Records the TeamMeta CREATE; can die on it (crash-before-create)."""

    def __init__(self, rec: _Recorder, name: str, crash: bool = False) -> None:
        self._rec = rec
        self._name = name
        self._crash = crash

    def query(self, q, params=None, **kw):
        if "CREATE" in q and "TeamMeta" in q:
            self._rec.events.append(("create_attempt", self._name))
            if self._crash:
                raise _ProcessKilled()  # process dies: no rollback, no journal
            self._rec.events.append(("create", self._name))
        return SimpleNamespace(result_set=[])

    def __getattr__(self, k):
        raise AttributeError(k)


class _FakeDb:
    def __init__(self, rec: _Recorder, crash_name: str | None = None) -> None:
        self._rec = rec
        self._crash_name = crash_name

    def select_graph(self, name: str) -> _FakeGraph:
        return _FakeGraph(self._rec, name, crash=(name == self._crash_name))


class _FakeReg:
    """Registry seam — the duplicate check reads ``result_set[0][0]``."""

    def query(self, q, params=None, **kw):
        return SimpleNamespace(result_set=[[False]])


def _fake_sdk(monkeypatch, rec: _Recorder, *, crash_name: str | None = None):
    """An uninitialised TortoiseSDK whose DB/registry/audit seams are fakes.

    Patches ``tortoise.projection._journal_append_product`` so the real seam
    (``journal_mint_write_ahead``) records into ``rec``.
    """
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setattr("tortoise.projection._journal_append_product", rec.journal)
    sdk = object.__new__(TortoiseSDK)
    monkeypatch.setattr(sdk, "_get_proj", lambda: SimpleNamespace(db=_FakeDb(rec, crash_name)))
    monkeypatch.setattr(sdk, "_get_registry", lambda: _FakeReg())
    monkeypatch.setattr(sdk, "_graph_create", lambda *a, **k: {})
    monkeypatch.setattr(sdk, "_audit", lambda *a, **k: None)
    return sdk


# ── ordering: journal BEFORE create ──────────────────────────────────────


def test_team_create_journals_before_the_teammeta_create(monkeypatch):
    """The effect (TeamMeta CREATE) must not precede the ownership line."""
    rec = _Recorder()
    sdk = _fake_sdk(monkeypatch, rec)

    res = sdk.org_create("wa_order")

    assert res["graph_name"] == "org_wa_order"
    assert rec.index(("journal", "org_wa_order")) < rec.index(("create", "org_wa_order")), (
        "ownership line must be journaled BEFORE the CREATE (write-ahead)"
    )


def test_crash_between_journal_and_create_leaves_no_orphan(monkeypatch):
    """A kill in the journal→create gap leaves the graph OWNED (no orphan).

    Pre-fix the ownership line was written after the CREATE, so this window
    contained a fully-created but unowned graph. Post-fix the line is already
    on disk when the process dies, so every journal-driven sweep can see it.
    """
    rec = _Recorder()
    sdk = _fake_sdk(monkeypatch, rec, crash_name="org_wa_crash")

    with pytest.raises(_ProcessKilled):
        sdk.org_create("wa_crash")

    assert ("journal", "org_wa_crash") in rec.events, (
        "write-ahead: the ownership line must already exist at the crash"
    )
    assert rec.index(("journal", "org_wa_crash")) < rec.index(("create_attempt", "org_wa_crash")), (
        "the journal line must precede the create ATTEMPT (not just its result)"
    )
    assert ("create", "org_wa_crash") not in rec.events  # effect never landed


def test_create_landing_then_crash_still_owns_the_graph(monkeypatch):
    """A crash AFTER the CREATE lands is still an owned graph.

    The old code journaled after the CREATE and before the next step; that
    post-create point is where kills leaked unowned graphs. With the line
    written ahead of the CREATE, any post-create crash is covered.
    """
    rec = _Recorder()
    sdk = _fake_sdk(monkeypatch, rec)

    def _kill(*_a, **_k):
        raise _ProcessKilled()

    monkeypatch.setattr(sdk, "_graph_create", _kill)

    with pytest.raises(_ProcessKilled):
        sdk.org_create("wa_after")

    assert ("journal", "org_wa_after") in rec.events
    assert ("create", "org_wa_after") in rec.events, (
        "the graph landed, and its ownership line was already written"
    )
    assert rec.index(("journal", "org_wa_after")) < rec.index(("create", "org_wa_after"))


def test_eager_provision_journals_before_materialization(monkeypatch):
    """The `_make_sdk(namespace=org_id)._get_proj()` lane MATERIALIZES
    org_{org_id} (Projection.__init__ -> _ensure_indexes, a query). The seam
    must precede BOTH the materialization and the CREATE — journaling only
    ahead of the CREATE would leave a materialization→journal kill window."""
    from tortoise import hosted_api

    order: list = []

    class _G:
        def query(self, q, params=None, **kw):
            if "CREATE" in q and "TeamMeta" in q:
                order.append("create")
            return SimpleNamespace(result_set=[[0]])  # probe: no TeamMeta yet

    class _Proj:
        db = SimpleNamespace(select_graph=lambda name: _G())

        def __init__(self):
            order.append("materialize")  # Projection.__init__ -> _ensure_indexes

    class _Sdk:
        def _get_proj(self):
            return _Proj()

    monkeypatch.setattr(hosted_api, "_make_sdk", lambda *a, **k: _Sdk())
    monkeypatch.setattr(
        "tortoise.projection._journal_append_product",
        lambda n, *a, **k: order.append(("journal", n)),
    )
    monkeypatch.setattr("tortoise.supabase_control.active_membership_org_ids", lambda cp, uid: [])

    assert hosted_api._eager_provision_org_graph(object(), "tid1", "N", "u1") == "org_tid1"
    assert order.index(("journal", "org_tid1")) < order.index("materialize"), (
        "journal must precede the projection's _ensure_indexes materialization"
    )
    assert order.index(("journal", "org_tid1")) < order.index("create")


def test_eager_provision_early_return_still_journals(monkeypatch):
    """The idempotency early-return path journals too.

    `_make_sdk(namespace=org_id)._get_proj()` has ALREADY materialized
    org_{org_id} by the time the probe runs, so the ownership line must
    precede it even when no CREATE follows. Documented cost: this session then
    owns (and sweeps) that graph — the write-ahead invariant is what keeps the
    materialized graph attributable (#3406 tracks the sweep-side protection).
    """
    from tortoise import hosted_api

    order: list = []

    class _G:
        def query(self, q, params=None, **kw):
            return SimpleNamespace(result_set=[[1]])  # TeamMeta already present

    class _Proj:
        db = SimpleNamespace(select_graph=lambda name: _G())

        def __init__(self):
            order.append("materialize")

    class _Sdk:
        def _get_proj(self):
            return _Proj()

    monkeypatch.setattr(hosted_api, "_make_sdk", lambda *a, **k: _Sdk())
    monkeypatch.setattr(
        "tortoise.projection._journal_append_product",
        lambda n, *a, **k: order.append(("journal", n)),
    )

    assert hosted_api._eager_provision_org_graph(object(), "tid1", "N", "u1") == "org_tid1"
    assert ("journal", "org_tid1") in order
    assert "create" not in order, "early return: no CREATE follows"
    assert order.index(("journal", "org_tid1")) < order.index("materialize")


# ── idempotency / convergence (real product consumers, no DB) ────────────


def test_duplicate_journal_lines_converge_to_one_owner(monkeypatch, tmp_path):
    """Replaying after a crash is idempotent: the journal is SET-valued.

    A retry re-journals the name (the list grows) but the owned set — what the
    per-test wipe delta and the peer-protection read consume — is unchanged,
    and every created graph is owned (``live <= owned``, the orphan invariant).
    """
    import tests._embedded as emb

    rec = _Recorder()
    sdk = _fake_sdk(monkeypatch, rec)

    sdk.org_create("wa_replay")
    sdk.org_create("wa_replay")  # retry / rebuild replays the same name

    journal_events = [e for e in rec.events if e == ("journal", "org_wa_replay")]
    assert len(journal_events) == 2, "retry re-journals the same name"
    assert rec.names("create") <= rec.names("journal"), "orphan: a created graph is not owned"
    # The REAL product consumer collapses the duplicate to one owned name.
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("org_wa_replay\norg_wa_replay\n")
    monkeypatch.setattr(emb, "_JOURNAL_FILE", str(journal))
    assert emb._created_since_last_wipe() == {"org_wa_replay"}


def test_created_since_last_wipe_collapses_duplicate_lines(tmp_path, monkeypatch):
    """The REAL per-test wipe delta (`tests._embedded._created_since_last_wipe`)
    is a set: a duplicate write-ahead line and a never-created name both
    collapse to one entry — replaying the journal is idempotent."""
    import tests._embedded as emb

    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("team_dup\nteam_dup\nteam_never\n")
    monkeypatch.setattr(emb, "_JOURNAL_FILE", str(journal))

    assert emb._created_since_last_wipe() == {"team_dup", "team_never"}


def test_sweep_drop_converges_on_a_never_created_name(tmp_path):
    """`_sweep_drop` (the REAL session sweep) drops a journaled name whose graph
    was never created, tolerates the duplicate line, and removes the journal —
    the journaled-but-never-created window converges instead of orphaning."""
    import tests._embedded as emb

    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("team_never\nteam_never\n")
    calls: list[tuple] = []

    class _G:
        def __init__(self, name):
            self._name = name

        def query(self, q, **kw):
            calls.append(("detach", self._name))
            return SimpleNamespace(result_set=[])

        def delete(self):
            calls.append(("delete", self._name))

    proj = SimpleNamespace(_host="localhost", db=SimpleNamespace(select_graph=lambda n: _G(n)))
    res = emb._sweep_drop(proj, str(journal), drop=True)

    assert res["journal_removed"] is True
    assert res["failed"] == []
    assert res["dropped"] == ["team_never"], "duplicate line collapses (seen-set)"
    assert ("delete", "team_never") in calls
    assert not journal.exists()


def test_journaled_without_create_persists_the_ownership_line(tmp_path, monkeypatch):
    """A journaled-but-never-created name stays owned.

    Write-ahead means the line can exist for a graph the CREATE never
    materialized. That is the harmless inverse of the orphan: the line is the
    tombstone the sweep drops an absent/empty graph by, so a rebuild replaying
    the journal converges (it re-drops a name with nothing to drop).
    """
    from tortoise.projection import journal_mint_write_ahead

    journal = tmp_path / "session.graphs.jsonl"
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    monkeypatch.setattr("tortoise.projection._TEST_SESSION_ACTIVE", True)

    journal_mint_write_ahead("team_never_created")

    assert journal.read_text().splitlines() == ["team_never_created"]


# ── source guard: every mint site, incl. the hosted + CLI lanes ──────────


def _namespace_arg(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == "namespace":
            return kw.value
    return None


def _graph_name_arg(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == "graph_name":
            return kw.value
    return None


def _is_materializing_sdk(call: ast.Call) -> bool:
    """`_make_sdk(...)` / `TortoiseSDK(...)` that builds a projection whose
    `.__init__` runs `_ensure_indexes()` (a query) and MATERIALIZES its graph.
    `namespace="registry"` / `None` target a non-team graph; `graph_name=<x>`
    is a full custom graph name (also materialized)."""
    f = call.func
    name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
    if name not in ("_make_sdk", "TortoiseSDK"):
        return False
    if _graph_name_arg(call) is not None:
        return True  # explicit full graph name → materialized on construction
    ns = _namespace_arg(call)
    if ns is None:
        return False
    return not (isinstance(ns, ast.Constant) and ns.value in (None, "registry"))


def _eager_bound_names(fn: ast.AST) -> set[str]:
    """Names bound from an `eager_init_query(...)` call in this function — so
    `q, p = eager_init_query(...); query(q)` is still recognized as a mint
    create without depending on the magic name `_init_q`."""
    names: set[str] = set()
    for n in ast.walk(fn):
        if not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        if not (isinstance(n.value, ast.Call) and _is_eager_init(n.value)):
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        for t in targets:
            for el in t.elts if isinstance(t, ast.Tuple) else [t]:
                if isinstance(el, ast.Name):
                    names.add(el.id)
    return names


def _is_mint_create(call: ast.Call, eager_names: set[str]) -> bool:
    """A `X.query(...)` that materializes a TeamMeta: the eager-init binding
    (any name bound from `eager_init_query`, incl. `_init_q`) or an inline
    `CREATE ... :TeamMeta` string. The `MATCH (m:TeamMeta)` probe is NOT a
    create."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "query"):
        return False
    if not call.args:
        return False
    a0 = call.args[0]
    if isinstance(a0, ast.Name) and a0.id in eager_names:
        return True
    return (
        isinstance(a0, ast.Constant)
        and isinstance(a0.value, str)
        and "CREATE" in a0.value
        and ":TeamMeta" in a0.value
    )


def _is_eager_init(call: ast.Call) -> bool:
    f = call.func
    return isinstance(f, ast.Attribute) and f.attr == "eager_init_query"


def _collect_sites(tree: ast.AST) -> dict[str, list[tuple[int, str]]]:
    """One pass: per (innermost) function name, a lineno-ordered event list of
    ``journal`` / ``create`` / ``eager_init`` / ``materialize`` events."""
    sites: dict[str, list[tuple[int, str]]] = {}

    def walk(node: ast.AST, fn: str | None, eager_names: set[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sites.setdefault(child.name, [])
                walk(child, child.name, _eager_bound_names(child))
                continue
            if isinstance(child, ast.Lambda):
                continue
            if fn is not None and isinstance(child, ast.Call):
                f = child.func
                if isinstance(f, ast.Name) and f.id == _WRITE_AHEAD_SEAM:
                    sites[fn].append((child.lineno, "journal"))
                if _is_mint_create(child, eager_names):
                    sites[fn].append((child.lineno, "create"))
                if _is_eager_init(child):
                    sites[fn].append((child.lineno, "eager_init"))
                if _is_materializing_sdk(child):
                    sites[fn].append((child.lineno, "materialize"))
            walk(child, fn, eager_names)

    walk(tree, None, set())
    return sites


def test_mint_sites_journal_before_create_and_materialization():
    """AST guard over sdk.py + hosted_api.py + __main__.py.

    For every function that mints a TeamMeta graph:
      (a) it journals (eager-init-only sites included), and
      (b) each CREATE has a journal after the previous CREATE (nearest-preceding
          coverage, so a two-branch site like ``register_user`` must journal in
          BOTH branches), and
      (c) every target materialization (``_make_sdk(namespace=<team>)`` /
          ``TortoiseSDK(namespace=<team>)``, whose ``._get_proj()`` materializes
          the graph) has a journal before it.

    The site set is asserted exactly: a new mint site must adopt the seam and be
    registered in ``_MINT_SITES``.
    """
    found: set[tuple[str, str]] = set()
    for rel in _GUARDED_FILES:
        tree = ast.parse((REPO / rel).read_text())
        for fn_name, events in _collect_sites(tree).items():
            events = sorted(events)
            creates = [ln for ln, kind in events if kind == "create"]
            if not creates and not any(k == "eager_init" for _, k in events):
                continue
            found.add((rel, fn_name))
            journals = [ln for ln, kind in events if kind == "journal"]
            assert journals, (
                f"{rel}::{fn_name} mints a TeamMeta with NO "
                f"{_WRITE_AHEAD_SEAM} call — record-after-effect regression"
            )
            # (b) nearest-preceding-journal coverage, per create.
            prev_create = -1
            for c in creates:
                assert any(prev_create < j < c for j in journals), (
                    f"{rel}::{fn_name} CREATE at line {c} has no "
                    f"{_WRITE_AHEAD_SEAM} call after the previous CREATE "
                    f"(line {prev_create}) — orphan window at that branch"
                )
                prev_create = c
            # (c) materialization coverage: journal before any team SDK build.
            for m in (ln for ln, kind in events if kind == "materialize"):
                assert any(j < m for j in journals), (
                    f"{rel}::{fn_name} materializes team_<ns> at line {m} "
                    f"(via _make_sdk/TortoiseSDK(namespace=...)._get_proj(), "
                    f"whose _ensure_indexes queries) BEFORE any "
                    f"{_WRITE_AHEAD_SEAM} — unjournaled graph window"
                )

    assert found == _MINT_SITES, (
        "mint-site set changed — adopt the write-ahead seam and update "
        f"_MINT_SITES. added={sorted(found - _MINT_SITES)} "
        f"removed={sorted(_MINT_SITES - found)}"
    )


def test_hosted_mint_sites_drop_the_raw_post_create_appender():
    """hosted_api must journal mints via the write-ahead seam, never the raw
    post-create appender (AST-checked, so a passing comment cannot satisfy it;
    both `name(...)` and `mod.name(...)` spellings are caught)."""
    tree = ast.parse((REPO / "tortoise/hosted_api.py").read_text())
    raw = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if (isinstance(f, ast.Name) and f.id == "_journal_append_product") or (
            isinstance(f, ast.Attribute) and f.attr == "_journal_append_product"
        ):
            raw.append(n.lineno)
    assert not raw, (
        "hosted_api still calls the raw post-create appender at line(s) "
        f"{raw} — mint journaling must go through journal_mint_write_ahead"
    )


def test_no_teammeta_create_outside_the_guarded_mint_files():
    """A new `CREATE ... :TeamMeta` mint must not ship in an unguarded file.

    The AST guard only scans `_GUARDED_FILES`; this repo-wide scan is the
    backstop that catches a mint site added in any other `tortoise/` module
    (the guard's `_MINT_SITES` assertion cannot see it)."""
    offenders = []
    for path in sorted((REPO / "tortoise").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in _GUARDED_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if "CREATE" in line and ":TeamMeta" in line:
                offenders.append(f"{rel}:{i}")
    assert not offenders, (
        "TeamMeta CREATE outside the guarded mint files — journal it via "
        "journal_mint_write_ahead and add the file to _GUARDED_FILES + the "
        f"site to _MINT_SITES: {offenders}"
    )
