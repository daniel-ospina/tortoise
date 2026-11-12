"""#3554 — the ``:Session`` write-schema declaration and its inventory predicate.

``tortoise/session_schema.py`` names this file as the enforcement of its own
claim ("``tools/session_retrofit_audit.py inventory`` re-runs the recorded
search predicate over the tree and diffs what it finds against
``SESSION_WRITERS`` / ``SESSION_CLIENTS`` … and
``tests/test_session_schema_3554.py`` fails on it"). Two halves, both guarded
here:

* the **inventory** half — the predicate must be CLEAN on the tree it ships
  with. The three false-positive mechanisms that made its clean verdict
  UNREACHABLE are pinned individually, not only through the aggregate: the
  declaration module quoting its own sites, a docstring quoting a write shape,
  and an HTTP-side handler DECORATOR / sibling route counted as a client;
* the **retrofit** half — ``audit_session_retrofit``'s report shape and the CLI
  that prints it, including the metric key the CLI reads (a key that did not
  exist once, and crashed the verb before it printed anything).

The full-tree scan is genuinely expensive (hundreds of files), so it runs ONCE
in a module-scoped fixture and every aggregate assertion shares it. No graph
and no live SDK: ``tortoise/session_schema.py`` is deliberately dependency-free,
so the retrofit CLI is exercised against a fake graph handle injected through a
fake ``tortoise.sdk`` module rather than a real one.
"""
from __future__ import annotations

import ast
import inspect
import itertools
import json
import re
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from tests import _wiring_gate_marks
from tools import session_retrofit_audit as audit
from tortoise import session_schema

REPO_ROOT = Path(__file__).resolve().parent.parent
# The synthetic-tree test below loads the REAL predicate module by path, because a
# temp directory is not the repo root and `tests` is not importable from there.
_WIRING_GATE_MARKS_SOURCE = Path(_wiring_gate_marks.__file__)


@pytest.fixture(scope="module")
def repo_sites() -> tuple[list, list]:
    """ONE full scan of the real tree; the scan is the cost, not the assert."""
    return audit.scan_graph_writers(REPO_ROOT), audit.scan_http_clients(REPO_ROOT)


@pytest.fixture(scope="module")
def repo_inventory(repo_sites) -> dict:
    """The report, built from the ONE scan above rather than a second walk."""
    writers, clients = repo_sites
    return audit.inventory_report(REPO_ROOT, writers=writers, clients=clients)


# ── the inventory half ─────────────────────────────────────────────────────


def test_inventory_is_clean_on_this_tree(repo_inventory: dict) -> None:
    """The predicate and the declaration must agree, in BOTH directions."""
    assert repo_inventory["ok"], (
        "the :Session writer/client predicate drifted from the declaration:\n"
        f"  undeclared writers: {repo_inventory['graph_writers_undeclared']}\n"
        f"  stale writers:      {repo_inventory['graph_writers_stale']}\n"
        f"  undeclared clients: {repo_inventory['http_clients_undeclared']}\n"
        f"  stale clients:      {repo_inventory['http_clients_stale']}\n"
        "Declare the new site (or drop the stale row) in "
        "tortoise/session_schema.py in the same reviewed diff.")


def test_declaration_module_is_not_its_own_site(repo_inventory: dict) -> None:
    """``tortoise/session_schema.py`` DECLARES sites; it never is one.

    Its ``note=`` strings quote a writer's Cypher and its ``marker=`` strings
    quote a client's call. Scanning the file reports the declaration as an
    undeclared writer/client the inventory can never clear.
    """
    assert (REPO_ROOT / audit.DECLARATION_MODULE).exists()
    assert ("tortoise/session_schema.py::SESSION_WRITERS"
            not in repo_inventory["graph_writers_found"])
    assert "tortoise/session_schema.py" not in repo_inventory["http_clients_found"]


def test_ask_fixture_declares_the_writer_not_its_docstring(
        repo_inventory: dict) -> None:
    """The ask fixture's declared row is the function that WRITES."""
    found = set(repo_inventory["graph_writers_found"])
    assert "tools/ask_spotcheck.py::merge_capture_session" in found
    assert "tools/ask_spotcheck.py::seed_capture_turn_store" not in found


def test_declared_http_client_markers_are_present() -> None:
    """A declared row cannot survive its call site being deleted silently."""
    for client in session_schema.SESSION_CLIENTS:
        text = (REPO_ROOT / client.path).read_text(encoding="utf-8")
        assert client.marker in text, (client.path, client.marker)


def test_declared_clients_name_every_shipped_hook_recorder() -> None:
    """Every shipped hook recorder is a declared client.

    ``SESSION_CLIENTS``' non-http rows exist so the inventory answers "who can
    create a session?", not only "who runs the Cypher?" — but
    ``inventory_report`` diffs only the ``transport == "http"`` rows, so a
    missing ``cli`` row is invisible to every other guard. Pre-fix two of the
    four shipped recorders were absent (Claude and Codex; only pi and cursor
    were declared).
    """
    declared = {c.path for c in session_schema.SESSION_CLIENTS}
    for path in ("tortoise/pi-hooks/tortoise-capture.ts",
                 "tortoise/claude-hooks/session-end.sh",
                 "tortoise/codex-hooks/session-end.sh",
                 "tortoise/cursor-hooks/session-end.sh"):
        assert path in declared, path


def test_scanner_scope_is_the_declared_predicate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The walk covers the WHOLE tree minus the DECLARED exclusions.

    Pre-fix the scope was a private 11-directory allow-list inside the tool
    while every statement of the predicate described it as "the tree minus
    tests/ and docs/": a writer under any unlisted tracked directory
    (``website/``, ``apps/``, ``operations/``) was invisible, so ``inventory``
    printed INVENTORY CLEAN with an undeclared writer present — the fail-open
    direction of a guard whose whole purpose is to be trusted. This pins both
    halves of the declared scope: the default walk reaches a directory outside
    the old allow-list, and each declared field drives the walk. Pre-fix the
    first assertion fails (the allow-list does not contain ``website/``).
    """
    pkg = tmp_path / "website" / "src"
    pkg.mkdir(parents=True)
    (pkg / "writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "website/src/writer.py::QUERY"}
    # ``excluded`` decides the path prefixes...
    monkeypatch.setitem(audit.PREDICATE, "excluded", ("website/",))
    assert audit.scan_graph_writers(tmp_path) == []
    monkeypatch.setitem(audit.PREDICATE, "excluded", ())
    # ...and ``skip_parts`` decides the directory names.
    monkeypatch.setitem(audit.PREDICATE, "skip_parts", ("src",))
    assert audit.scan_graph_writers(tmp_path) == []


def test_worktree_scope_is_the_tracked_corpus_not_disk_copies(
        tmp_path: Path) -> None:
    """A linked worktree's copy of the tree is not a second site.

    ``.worktrees/<lane>/`` is this repo's documented, gitignored project-local
    worktree root (``.gitignore:3``; ``operations/skills/using-git-worktrees``).
    Each lane is a full copy of the source tree, so a filesystem walk reports
    every writer at a second, undeclared path
    (``.worktrees/<lane>/tortoise/sdk.py::…``) and ``inventory`` prints
    INVENTORY DRIFT on the repo's own canonical checkout (189 lanes there) while
    ``test_inventory_is_clean_on_this_tree`` reds on a tracked tree that is
    clean. The corpus is therefore the ``git ls-files`` tracked scope, not
    "whatever is on disk". Pre-fix this test fails: the untracked lane copy is
    scanned as a site.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    lane = tmp_path / ".worktrees" / "lane-1" / "tortoise"
    lane.mkdir(parents=True)
    (lane / "sdk.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "tortoise/sdk.py"],
                   check=True)
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/sdk.py::QUERY"}


def test_tracked_scan_applies_the_declared_skip_parts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``skip_parts`` narrows the TRACKED corpus, not only the fallback walk.

    ``_iter_source`` reads ``skip_parts`` from :data:`PREDICATE` — but pre-fix
    it consulted the declared directory names only in its non-git fallback, so
    on the path CI and every lane actually use (a real work tree, ``git
    ls-files``) a TRACKED file under a declared-out directory was still a site.
    ``website/apps/blog-admin/dist/assets/*.js`` is tracked today, so a future
    vendor bundle carrying a ``:Session`` shape (or the capture route) would
    read as INVENTORY DRIFT on a non-site. Pre-fix this test fails:
    ``dist/writer.py::QUERY`` is scanned as a site.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    generated = tmp_path / "dist"
    generated.mkdir()
    (generated / "writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add",
                    "tortoise/sdk.py", "dist/writer.py"], check=True)
    # The TRACKED branch is the one under test: this root IS a work tree.
    assert audit._tracked_files(tmp_path) is not None
    assert "dist" in audit.PREDICATE["skip_parts"]
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/sdk.py::QUERY"}
    # ...and the DECLARED field really decides it: drop the declaration and
    # the tracked file becomes a site again.
    monkeypatch.setitem(audit.PREDICATE, "skip_parts", ())
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "dist/writer.py::QUERY", "tortoise/sdk.py::QUERY"}


def test_declaration_module_skip_is_root_independent(tmp_path: Path) -> None:
    """The declaration module is skipped for a scan rooted anywhere.

    ``--root tortoise`` (or any root other than the repo root) previously
    compared ``path.relative_to(root)`` against the fixed repo-relative
    ``tortoise/session_schema.py``, so it never matched and the declaration's
    own ``note=``/``marker=`` strings became sites — INVENTORY DRIFT for a scan
    of the declaration's own directory. Pre-fix this test fails with
    ``['session_schema.py::NOTE']``.
    """
    root = tmp_path / "tortoise"
    root.mkdir()
    (root / "session_schema.py").write_text(
        'NOTE = "MATCH (s:Session) WHERE s.is_episodic IS NULL '
        'SET s.is_episodic = true"\n',
        encoding="utf-8")
    assert audit.scan_graph_writers(root) == []


def test_subdir_scan_keys_sites_repo_relatively_and_compares_in_scope(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--root <subdir>`` keys sites repo-relatively and verifies only them.

    ``git ls-files`` prints paths relative to the INVOCATION directory, so a
    scan rooted at ``tortoise/`` pre-fix minted ``sdk.py::…`` where the
    declaration says ``tortoise/sdk.py::…``: every found site read as
    undeclared AND every declared row as stale, and ``inventory`` printed
    INVENTORY DRIFT for a clean tree. The tracked branch is the one under test
    (the root really is a work tree), matching the neighbouring test. Pre-fix
    this test fails on the first key assertion.
    """
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    (tmp_path / "tortoise" / "capture.py").write_text(
        'import requests\n'
        'def post_capture(base):\n'
        '    return requests.post(f"{base}/v1/sessions", json={})\n',
        encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sub = tmp_path / "tortoise"
    assert audit._tracked_files(sub) is not None       # the tracked branch
    assert {s.key for s in audit.scan_graph_writers(sub)} == {
        "tortoise/sdk.py::QUERY"}
    assert {s.path for s in audit.scan_http_clients(sub)} == {
        "tortoise/capture.py"}
    # The comparison is scoped to the scan root: a declared row OUTSIDE the
    # subtree is not verifiable from it, so it must not read as STALE. The
    # toplevel scan verifies the full declaration instead.
    monkeypatch.setattr(audit, "SESSION_WRITERS", (
        types.SimpleNamespace(key="tortoise/sdk.py::QUERY"),
        types.SimpleNamespace(key="outside/other.py::WRITER"),
    ))
    monkeypatch.setattr(audit, "SESSION_CLIENTS", (
        types.SimpleNamespace(transport="http", path="tortoise/capture.py"),
    ))
    sub_report = audit.inventory_report(sub)
    assert sub_report["ok"], sub_report
    assert "outside/other.py::WRITER" not in sub_report["graph_writers_declared"]
    assert not audit.inventory_report(tmp_path)["ok"]


#: The declared writers whose property names the scanner CANNOT read at its site:
#: each assembles its ``SET`` clause from something other than the one Cypher
#: literal the scanner reads (an f-string built per property, or — for
#: ``_write_session_and_turns`` — 11 separate ``merge_sets.append("s.harness=…")``
#: literals behind a final f-string ``MERGE``), so ``Site.properties`` is empty
#: and ``test_scanner_properties_match_the_declaration`` cannot compare the row.
#: Their property sets are hand-declared and verified by a bespoke AST pin named
#: in ``_ROW_VERIFIERS`` — an exemption here is a claim that a verifier COVERS it,
#: and ``test_every_exempt_row_has_a_row_verifier`` fails if one does not.
#: The whole-schema writer is excluded separately — it is the ONE row declared
#: ``writes_whole_schema=True`` (``SET s += $props``), so there is no property
#: name at the site to compare. Since #9124 the write lives in
#: ``projection._rebuild_all_replay`` (``rebuild_all`` is the entry point).
#:
#: ``tortoise/sdk.py::_write_session_and_turns`` was exempted on the reason that
#: it "builds its ``SET`` clause with an f-string", which is false and made the
#: exemption read as "nothing to read" for the single largest row in the
#: declaration — the shared capture primitive both capture lanes funnel through
#: (#3551). Neither the scanner nor this census could compare it, and no test
#: did: dropping a property from the row (plus the matching §4.8 cell, which the
#: doc mirror forces you to make) passed every test in this file. The reason is
#: now the true one and the row is pinned by
#: ``test_write_session_and_turns_declares_every_property_it_writes``.
_FSTRING_WRITERS: frozenset[str] = frozenset({
    "tools/ask_spotcheck.py::merge_capture_session",
    "tortoise/projection/entities.py::_fold_session_recorded",
    "tortoise/sdk.py::_write_session_and_turns",
})

#: The bespoke row-level verifier for each scanner-unreadable declared writer.
#: Read by ``test_every_exempt_row_has_a_row_verifier``; each named test asserts
#: the declared property set equals the names its own function's literals carry.
_ROW_VERIFIERS: dict[str, str] = {
    "tools/ask_spotcheck.py::merge_capture_session":
        "test_merge_capture_session_declares_every_property_it_writes",
    "tortoise/projection/entities.py::_fold_session_recorded":
        "test_fold_session_recorded_declares_every_property_it_writes",
    "tortoise/sdk.py::_write_session_and_turns":
        "test_write_session_and_turns_declares_every_property_it_writes",
}


def test_scanner_properties_match_the_declaration(repo_sites) -> None:
    """Every writer whose literal carries property names declares exactly them.

    ``inventory_report`` diffs writer KEYS only, so a declared property set that
    disagrees with the properties its site writes is invisible to the inventory
    — the drift the §4.8 owner column and this declaration exist to answer.
    The exemption for the f-string writers is itself pinned: a new writer cannot
    join the unreadable set unnoticed.
    """
    writers, _ = repo_sites
    scanned: dict[str, set[str]] = {}
    for site in writers:
        scanned.setdefault(site.key, set()).update(site.properties)
    mismatched: dict[str, dict[str, list[str]]] = {}
    for writer in session_schema.SESSION_WRITERS:
        if writer.writes_whole_schema or not scanned.get(writer.key):
            continue
        if scanned[writer.key] != set(writer.properties):
            mismatched[writer.key] = {
                "declared_only": sorted(set(writer.properties)
                                        - scanned[writer.key]),
                "site_only": sorted(scanned[writer.key]
                                    - set(writer.properties)),
            }
    assert not mismatched, mismatched
    unreadable = {
        w.key for w in session_schema.SESSION_WRITERS
        if not w.writes_whole_schema and not scanned.get(w.key)}
    assert unreadable == _FSTRING_WRITERS, (
        "only the declared f-string writers may have no literal property "
        f"names; got {sorted(unreadable)}")


def test_ast_index_excludes_only_the_docstring_constant() -> None:
    """A docstring is an AST ``Constant``; it is excluded BY IDENTITY."""
    tree = ast.parse(
        'def seed(sdk):\n'
        '    """MERGE (s:Session {id:$sid}) SET s.turn_count = 1."""\n'
        '    QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n'
        '    return QUERY\n')
    index = audit._ast_index(tree)
    assert len(index.literals) == 2
    doc_literals = [n for n in index.literals if id(n) in index.docstrings]
    kept = [n for n in index.literals if id(n) not in index.docstrings]
    assert len(doc_literals) == 1
    assert doc_literals[0].value.startswith("MERGE")
    assert len(kept) == 1                  # the sibling literal survives


def test_declaration_module_path_is_skipped(tmp_path: Path) -> None:
    """``session_schema.py`` is excluded from BOTH halves by path.

    Its ``note=`` is a writer-shaped literal and its ``SessionClient(...)``
    call carries a client-shaped ``marker=`` — the two self-matches that made
    the real tree's inventory permanently red.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "session_schema.py").write_text(
        'NOTE = "MATCH (s:Session) WHERE s.is_episodic IS NULL '
        'SET s.is_episodic = true"\n'
        'ROWS = SessionClient(marker=\'post(cfg, "/v1/sessions", payload\')\n',
        encoding="utf-8")
    assert audit.scan_graph_writers(tmp_path) == []
    assert audit.scan_http_clients(tmp_path) == []


def test_docstring_mention_is_not_a_writer_site(tmp_path: Path) -> None:
    """A docstring that QUOTES a write shape is not a writer."""
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "seeder.py").write_text(
        'def seed(sdk):\n'
        '    """MERGE (s:Session {id:$sid}) SET s.turn_count = 1."""\n'
        '    raise NotImplementedError\n',
        encoding="utf-8")
    assert audit.scan_graph_writers(tmp_path) == []
    # …and the exclusion is not over-broad: the same literal in real code IS a
    # site, so the test above cannot pass by weakening the predicate entirely.
    (pkg / "real_writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/real_writer.py::QUERY"}


def test_label_prefilter_admits_every_spelling_the_predicate_matches(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The prefilter is a SUPERSET of the alias/verb matchers, never a gate.

    ``(s: Session {`` is legal Cypher (``oC_NodeLabel : ':' SP? oC_LabelName``)
    and every stage AFTER the prefilter accepts it — :data:`_SESSION_ALIAS_RE`
    and both declared write verbs spell the colon with ``\\s*`` — but the
    prefilter that GATES them tested the declaration as an EXACT SUBSTRING, so
    the tolerance was dead: a writer spelled with whitespace after the colon
    was invisible and ``inventory`` printed INVENTORY CLEAN with an undeclared
    writer present, the fail-open mirror of the false-positive class the
    tracked-corpus fix removed. Pre-fix this test fails: no site is found.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "spaced.py").write_text(
        'QUERY = "MERGE (s: Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/spaced.py::QUERY"}
    # …and the prefilter is still the DECLARED label, not "admit everything":
    # a different label name is not a site (the ``\\b`` is load-bearing).
    (pkg / "spaced.py").write_text(
        'QUERY = "MERGE (s: Sessionish {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert audit.scan_graph_writers(tmp_path) == []
    # …and the pattern is compiled from the DECLARATION, not a private literal:
    # moving the declared label makes the prefilter stop admitting the site it
    # just admitted (a hardcoded ``:Session`` here would keep finding it).
    monkeypatch.setitem(audit.PREDICATE, "label", ":Other")
    monkeypatch.setitem(audit.PREDICATE, "write_verbs",
                        (r"MERGE\s*\(\s*<alias>\s*:\s*Session",
                         r"\bSET\s+<alias>\."))
    (pkg / "spaced.py").write_text(
        'QUERY = "MERGE (s: Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert audit.scan_graph_writers(tmp_path) == []


def test_route_definition_and_sibling_routes_are_not_client_sites(
        tmp_path: Path) -> None:
    """Only a CALL to the capture route is a client; its definition is not.

    ``@app.post("/v1/sessions")`` is the server that serves the route, and
    ``/v1/sessions/commit`` is a DIFFERENT route. Counting either as a capture
    client is the false positive that made the inventory permanently red.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "capture_client.py").write_text(
        'import requests\n'
        'def post_capture(base):\n'
        '    return requests.post(f"{base}/v1/sessions", json={})\n',
        encoding="utf-8")
    (pkg / "commit_client.py").write_text(
        'import requests\n'
        'def post_commit(base):\n'
        '    return requests.post(f"{base}/v1/sessions/commit", json={})\n',
        encoding="utf-8")
    (pkg / "route_server.py").write_text(
        'app = None\n'
        '@app.post("/v1/sessions")\n'
        'def handler():\n'
        '    return None\n',
        encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/capture_client.py"}


def test_default_method_reads_of_the_route_are_not_client_sites(
        tmp_path: Path) -> None:
    """A GET of the route is not a capture client.

    ``fetch("/v1/sessions")`` and ``urllib.request.Request(url)`` both default
    to GET, so the route alone is not a capture CALL — a reader (the dashboard's
    session list uses exactly the ``fetch`` shape) was otherwise a false
    INVENTORY DRIFT whose only clearable answer is to declare a read as a
    client. Pre-fix this test fails: both read files are reported as sites.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "list_sessions.js").write_text(
        'const r = fetch("/v1/sessions");\n', encoding="utf-8")
    (pkg / "list_sessions.py").write_text(
        'import urllib.request as u\n'
        'def list_sessions(base):\n'
        '    return u.urlopen(u.Request(f"{base}/v1/sessions"))\n',
        encoding="utf-8")
    assert audit.scan_http_clients(tmp_path) == []
    # …and the POST shapes are still sites, so the assertion above cannot pass
    # by deleting the two matchers: the POST evidence is what decides.
    (pkg / "post_sessions.js").write_text(
        'const r = fetch("/v1/sessions", { method: "POST", body: "{}" });\n',
        encoding="utf-8")
    (pkg / "post_sessions.py").write_text(
        'import urllib.request as u\n'
        'def post_sessions(base, payload):\n'
        '    return u.urlopen(u.Request(f"{base}/v1/sessions", data=payload,\n'
        '                               method="POST"))\n',
        encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/post_sessions.js", "tortoise/post_sessions.py"}


def test_multiline_post_calls_are_still_client_sites(tmp_path: Path) -> None:
    """A POST whose evidence sits on ANOTHER line is still a client site.

    The POST evidence was tested per PHYSICAL line, so the route on one line
    and ``method: "POST"`` on the next — the shape a formatter produces for
    ``fetch(url, {\n  method: "POST",\n  …\n});``, and the idiomatic spelling
    in this repo's TS — was invisible: ``inventory`` printed INVENTORY CLEAN
    with an undeclared client present, the fail-OPEN mirror of the
    false-positive class the same commit removed. A backslash-continued
    ``curl`` is the same defect for the shell transport. Pre-fix this test
    fails: none of the three files is a site.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "multiline_fetch.ts").write_text(
        'const r = fetch("/v1/sessions", {\n'
        '  method: "POST",\n'
        '  body: JSON.stringify(payload),\n'
        '});\n', encoding="utf-8")
    (pkg / "multiline_post.ts").write_text(
        'post(\n  cfg,\n  "/v1/sessions",\n  payload,\n);\n',
        encoding="utf-8")
    (pkg / "wrapped_curl.sh").write_text(
        'curl \\\n  -X POST \\\n'
        '  -H "content-type: application/json" \\\n'
        '  "$BASE/v1/sessions" \\\n  -d @payload.json\n', encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/multiline_fetch.ts", "tortoise/multiline_post.ts",
        "tortoise/wrapped_curl.sh"}
    # …and a multi-line READ stays a non-site, so the assertions above cannot
    # pass by dropping the POST requirement entirely.
    (pkg / "multiline_fetch.ts").write_text(
        'const r = fetch("/v1/sessions", {\n'
        '  headers: { accept: "application/json" },\n'
        '});\n', encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/multiline_post.ts", "tortoise/wrapped_curl.sh"}


def test_multiline_call_with_an_apostrophe_comment_is_still_a_site(
        tmp_path: Path) -> None:
    """An apostrophe in a comment must not unbalance the call span.

    ``_balanced_span`` entered a string on ANY ``'``/``"``/backtick, so a
    multi-line call carrying an ODD number of those characters inside a
    comment (``// the server's session endpoint``, ``// don't retry``) never
    balanced: the call was dropped and the per-logical-line net could not see
    the POST evidence on its own line. Pre-fix this test fails: no site.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "line_comment.ts").write_text(
        'const r = fetch("/v1/sessions", {\n'
        "  // the server's session endpoint — don't retry\n"
        '  method: "POST",\n'
        '  body: JSON.stringify(payload),\n'
        '});\n', encoding="utf-8")
    (pkg / "block_comment.ts").write_text(
        'const r = fetch("/v1/sessions", {\n'
        "  /* the client's capture call */\n"
        '  method: "POST",\n'
        '});\n', encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/line_comment.ts", "tortoise/block_comment.ts"}
    # …and the balanced-span fix does not swallow a genuinely unterminated
    # call: a span with no closing paren stays dropped, so the assertions
    # above cannot pass by making ``_balanced_span`` unconditional.
    assert audit._balanced_span('fetch("x", ', 5) == -1
    assert audit._balanced_span('fetch("x")', 5) == 10


def test_lowercase_method_evidence_is_still_a_client_site(
        tmp_path: Path) -> None:
    """``method: "post"`` is evidence too — Fetch uppercases the method.

    The POST evidence was matched CASE-SENSITIVELY, so a working
    ``fetch(url, { method: "post" })`` (the Fetch standard normalizes
    ``method`` to byte-uppercase) contributed no site and ``inventory``
    printed INVENTORY CLEAN with an undeclared client present. Pre-fix this
    test fails: no site is found.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "lower_fetch.ts").write_text(
        'const r = fetch("/v1/sessions", {\n'
        '  method: "post",\n'
        '  body: JSON.stringify(payload),\n'
        '});\n', encoding="utf-8")
    (pkg / "lower_request.py").write_text(
        'import urllib.request as u\n'
        'def post_sessions(base, payload):\n'
        '    return u.urlopen(u.Request(f"{base}/v1/sessions", data=payload,\n'
        '                               method="post"))\n', encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/lower_fetch.ts", "tortoise/lower_request.py"}


def test_a_get_plus_an_unrelated_post_object_on_one_line_is_not_a_site(
        tmp_path: Path) -> None:
    """A line-scoped call matcher fuses two statements into one client.

    The non-Python half unioned the balanced call spans with EVERY logical
    line. The matchers' gap is ``[\\s\\S]*?`` (needed for call spans), so over
    a line span it crosses the call's own ``)``: a GET of the route followed by
    an unrelated ``{method: "POST"}`` object on the same physical line matched
    as one capture client — a false INVENTORY DRIFT on a non-site. Pre-fix
    this test fails: ``tortoise/fused.js`` is reported.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "fused.js").write_text(
        'var a=fetch("/v1/sessions"),b={method:"POST",body:x};\n',
        encoding="utf-8")
    assert audit.scan_http_clients(tmp_path) == []
    # …and the real multi-line POST is still a site, so the assertion above
    # cannot pass by weakening the matchers.
    (pkg / "fused.js").write_text(
        'var a=fetch("/v1/sessions",{\n  method:"POST",\n  body:x});\n',
        encoding="utf-8")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/fused.js"}


def test_inventory_cli_reports_clean_and_exits_zero(
        repo_inventory: dict, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The CLI's verdict/exit mapping, without a second full scan."""
    monkeypatch.setattr(audit, "inventory_report", lambda root: repo_inventory)
    rc = audit.main(["inventory"])
    assert rc == 0
    assert capsys.readouterr().out.rstrip().endswith("INVENTORY CLEAN")


def test_inventory_json_stdout_is_only_json(tmp_path: Path, capsys) -> None:
    """``--json`` is the machine-readable form: the payload and nothing else.

    An empty root matches no declaration, so the verdict is DRIFT — but the
    point is the parse: pre-fix the appended verdict line broke ``json.loads``.
    """
    rc = audit.main(["inventory", "--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False


# ── the retrofit half ──────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self.result_set = rows


class _FakeGraph:
    """The five reads ``audit_session_retrofit`` issues, answered from fixtures.

    Two short sessions under ``capture_ok``, only ONE of them ``capture_ok``
    true — so ``capture_ok_true`` (1) and ``all_short`` (2) differ and a test
    can tell which key the CLI printed.

    The (c) branch is DELIBERATELY universe-sensitive: it reports an
    under-count only when the query reaches the turn points through
    ``CONTAINS``, because the recorded counter sums the Session-origin AND the
    turn-Point-origin edges (``link_session_entities``). A Session-origin-only
    query would understate the gap — and can print a clean zero for a real one.
    """

    def query(self, cypher: str, params: dict | None = None) -> _FakeResult:
        if "stored_turns < s.turn_count" in cypher:
            return _FakeResult([
                ("s-short-ok", 5, 2, True, "llm", "claude", "hosted"),
                ("s-short-fail", 5, 2, False, "llm", "claude", "sdk"),
            ])
        if "live_edges > s.entity_links_created" in cypher:
            if "CONTAINS" not in cypher:
                return _FakeResult([])
            return _FakeResult([("s-links", 1, 3, 4)])
        if "s.turn_count IS NOT NULL RETURN count(s)" in cypher:
            return _FakeResult([[2]])
        if "s.capture_ok = true RETURN count(s)" in cypher:
            return _FakeResult([[1]])
        if "MATCH (s:Session) RETURN count(s)" in cypher:
            return _FakeResult([[2]])
        raise AssertionError(f"unexpected query: {cypher}")


class _FakeSDK:
    def __init__(self, *args, **kwargs) -> None:
        self.graph = _FakeGraph()
        self.closed = False
        # What the real ``TortoiseSDK._get_proj`` resolves from the URI /
        # namespace. The retrofit verb must read it rather than printing
        # ``graph: None`` on the ``--uri`` path (#3554 review).
        self.graph_name: str | None = None

    def _get_proj(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(g=self.graph, graph_name=self.graph_name)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_sdk_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A fake ``tortoise.sdk`` — the schema module stays dependency-free."""
    module = types.ModuleType("tortoise.sdk")
    module.TortoiseSDK = _FakeSDK          # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tortoise.sdk", module)
    return module


def test_retrofit_verb_prints_the_capture_ok_true_short_count(
        fake_sdk_module: types.ModuleType, capsys) -> None:
    """The ``retrofit`` verb must print a metric, not raise ``KeyError``."""
    rc = audit.main(["retrofit", "--db", "fake-graph.db"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "capture_ok=true, short turn count : 1" in out
    assert "of 2 measured" in out


def test_retrofit_json_is_valid_json(
        fake_sdk_module: types.ModuleType, capsys) -> None:
    rc = audit.main(["retrofit", "--db", "fake-graph.db", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["short_turn_count"]["capture_ok_true"] == 1
    assert payload["short_turn_count"]["all_short"] == 2
    assert payload["entity_links_under_count"]["sessions"] == 1
    assert payload["verdicts"] == session_schema.RETROFIT_VERDICTS


def test_retrofit_verb_names_the_turn_store_and_the_record_held_bucket(
        fake_sdk_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch,
        capsys) -> None:
    """#10000: an operator must SEE which store the (a) count measured.

    A clean-looking ``(a)`` is unreadable without the mode: with the record
    holding the turns, a zero on that line means "nothing to measure here",
    not "no session lost turns". The bucket is printed beside it (with its
    ``retro_quantifiable`` flag) so the two can never be confused, and the
    zero-node session is named in it rather than silently dropped.
    """
    import tortoise.record_writer as record_writer

    monkeypatch.setattr(record_writer, "record_dsn",
                        lambda: "postgresql://record@127.0.0.1:5432/record")

    def factory(*args, **kwargs):
        sdk = _FakeSDK(*args, **kwargs)
        sdk.graph = _TwoShapeGraph()
        return sdk

    monkeypatch.setattr(fake_sdk_module, "TortoiseSDK", factory)
    rc = audit.main(["retrofit", "--db", "fake-graph.db"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "turns held outside the graph (record armed): True" in out
    assert "(a') zero turn nodes — record-held, NOT a shortfall : 1" in out
    assert "retro_quantifiable=False" in out
    assert "capture_ok=true, short turn count : 1" in out


def test_retrofit_uri_reports_the_resolved_graph_name(
        fake_sdk_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch,
        capsys) -> None:
    """``--uri`` without ``--namespace`` names the URI's resolved graph.

    The report's ``graph`` field is the artifact a reader uses to know WHAT
    was measured. On ``--uri`` the SDK resolves the graph name from
    ``TORTOISE_DB_URI`` (``_get_proj`` → ``_derive_graph_name``), but the verb
    passed ``args.namespace or args.db`` — both ``None`` — so the first line
    read ``graph: None``. Pre-fix this test fails.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://localhost:6379/mygraph")

    def factory(*args, **kwargs):
        sdk = _FakeSDK(*args, **kwargs)
        sdk.graph_name = "mygraph"      # what the SDK resolves from the URI
        return sdk

    monkeypatch.setattr(fake_sdk_module, "TortoiseSDK", factory)
    rc = audit.main(["retrofit", "--uri"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] == "graph: mygraph"
    # …and the machine-readable report carries the same name.
    rc = audit.main(["retrofit", "--uri", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["graph"] == "mygraph"


def test_retrofit_without_a_graph_refuses_instead_of_printing_a_zero(
        fake_sdk_module: types.ModuleType, capsys) -> None:
    """No graph → ``SystemExit`` and NOTHING on stdout.

    The module docstring and the plan amendment used to state that without a
    graph "the tool reports the classes as UNMEASURED", a path that does not
    exist: the verb refuses to invent a graph. This pins the actual behaviour
    (the sentence's intent) so the docs and the code cannot drift apart again —
    and so a later refactor cannot quietly start printing a zero for a graph
    nobody read.
    """
    with pytest.raises(SystemExit) as exc:
        audit.main(["retrofit"])
    assert "--db" in str(exc.value)
    assert capsys.readouterr().out == ""
    # The claim and the code agree: neither home still describes an UNMEASURED
    # *report*. Pre-fix both did, and no code path could produce one.
    assert "UNMEASURED" not in (audit.__doc__ or "")
    assert "UNMEASURED" not in PLAN.read_text(encoding="utf-8")


class _RecordingGraph(_FakeGraph):
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, cypher: str, params: dict | None = None) -> _FakeResult:
        self.queries.append(cypher)
        return super().query(cypher, params)

    def undercount_query(self) -> str:
        return next(q for q in self.queries
                    if "live_edges > s.entity_links_created" in q)


def test_undercount_counts_the_same_edge_universe_as_the_counter() -> None:
    """(c) must count Session-origin AND turn-Point-origin ``aboutObject`` edges.

    The recorded counter is ``link_session_entities``' ``created``, which sums
    BOTH ``_link(proj, "Session", …)`` and ``_link(proj, "Point", tid, …)``.
    A Session-origin-only count understates the gap and can print a clean ZERO
    for a real one: ``0 > 3`` is false, so a session holding 3 turn-point edges
    against a counter of 1 reads clean. Pre-fix this test fails — the fake
    graph answers the (c) query with an under-count only when it reaches the
    turn points through ``CONTAINS``.
    """
    report = session_schema.audit_session_retrofit(_RecordingGraph())
    assert report["entity_links_under_count"]["sessions"] == 1
    assert report["entity_links_under_count"]["edges"] == 2


def test_undercount_query_aggregates_the_two_legs_separately() -> None:
    """The two ``OPTIONAL MATCH``es must not cross-product their counts.

    Counting both legs in ONE row scope multiplies them
    (``session_edges x turn_edges``) and over-reports the gap — the
    mirror-image defect of the under-count. Each leg is aggregated in its own
    ``WITH`` stage before the sum.

    The assertions are structural, not substring presence: a single-scope
    cross-product query still CONTAINS ``count(r1) AS session_edges`` and
    ``count(r2) AS turn_edges``, so the pre-fix substrings-only test passed on
    the very shape it names. Here the session leg's aggregation must close
    before the turn leg's ``OPTIONAL MATCH`` opens.
    """
    graph = _RecordingGraph()
    session_schema.audit_session_retrofit(graph)
    q = graph.undercount_query()
    assert "CONTAINS" in q
    assert "pointKind = 'event'" in q and "is_episodic = true" in q
    assert "count(r1) AS session_edges" in q
    assert "count(r2) AS turn_edges" in q
    assert "count(r1) + count(r2)" not in q
    # The stage boundary: ``count(r1) AS session_edges`` must be emitted by a
    # WITH that precedes the turn leg's ``OPTIONAL MATCH (s)-[:CONTAINS]``.
    # In the single-scope cross-product the count lands AFTER that match, so
    # this fails on the shape the test exists to catch.
    leg1_aggregated = q.index("count(r1) AS session_edges")
    leg2_opens = q.index("OPTIONAL MATCH (s)-[:CONTAINS]")
    assert leg1_aggregated < leg2_opens, (
        "the session-edge count must be aggregated in its own WITH before the "
        "turn leg opens; counting both legs in one scope cross-products them "
        "and over-reports the gap")
    # ...and the aggregation is introduced by a WITH clause, not appended to
    # the first match's row scope.
    assert "WITH s, count(r1) AS session_edges" in q


# ── #10000: the graph may not be the turns' store ──────────────────────────


class _TwoShapeGraph(_FakeGraph):
    """(a) answered with the three shapes a real graph can hold at once.

    * ``s-armed``    — ``turn_count=5``, **no** turn member: the shape an
      ARMED capture leaves (§9.1 puts the turns in the record), which is
      ambiguous graph-side;
    * ``s-partial``  — ``turn_count=5``, 2 members: a shortfall the graph DOES
      witness, because some of this session's turns are nodes and fewer than
      its claim;
    * ``s-complete`` — not short at all, so it appears in no list.

    One graph, three arms, so the split cannot be satisfied by a fixture that
    only ever holds the shape it asserts.
    """

    def query(self, cypher: str, params: dict | None = None) -> _FakeResult:
        if "stored_turns < s.turn_count" in cypher:
            return _FakeResult([
                ("s-armed", 5, 0, True, "llm", "claude", "hosted"),
                ("s-partial", 5, 2, True, "llm", "claude", "hosted"),
            ])
        if "s.capture_ok = true RETURN count(s)" in cypher:
            return _FakeResult([[2]])
        if "s.turn_count IS NOT NULL RETURN count(s)" in cypher:
            return _FakeResult([[3]])
        if "MATCH (s:Session) RETURN count(s)" in cypher:
            return _FakeResult([[3]])
        return super().query(cypher, params)


def test_a_zero_node_session_is_not_a_shortfall_when_the_record_holds_turns() \
        -> None:
    """#10000: an armed capture must not read as a lost transcript.

    With the record holding the turns, ``s.turn_count`` counts Postgres rows
    and the graph holds NO turn member for a session the writer stored
    correctly. Pre-fix ``s-armed`` landed in ``short_turn_count`` — the class a
    retrofit/backfill acts on — so the audit's own report would have read as a
    mandate to write the transcript BACK into the graph, the placement §9.1/D1
    forbids. It is now its own bucket, listed by id.
    """
    report = session_schema.audit_session_retrofit(
        _TwoShapeGraph(), turns_in_record=True)
    held = report["turns_record_held"]
    assert [r["id"] for r in held["sample"]] == ["s-armed"]
    assert held["sessions"] == 1
    assert held["retro_quantifiable"] is False
    assert held["reason"] == session_schema.RETROFIT_VERDICTS[
        "turns_record_held"]["reason"]
    # …and it is NOT in the short class, while the graph-witnessed partial
    # shortfall stays there.
    assert [r["id"] for r in report["short_turn_count"]["sample"]] == [
        "s-partial"]
    assert report["short_turn_count"]["all_short"] == 1
    assert report["short_turn_count"]["capture_ok_true"] == 1
    assert report["short_turn_count"]["turns_missing"] == 3
    assert report["short_turn_count"]["turns_in_record"] is True
    # The denominator is untouched: it is every session that HAS a claim.
    assert report["short_turn_count"]["candidates_measured"] == 3


def test_an_unarmed_deployment_still_reports_a_zero_node_session_as_short() \
        -> None:
    """The transitional guarantee: with the graph as the turns' store, a
    zero-member session IS a shortfall, and the report is unchanged.

    Without this arm the fix would be a blanket suppression — it would hide a
    real store loss on every deployment that never arms the record, which is
    the population §9.1 is not yet true of.
    """
    report = session_schema.audit_session_retrofit(
        _TwoShapeGraph(), turns_in_record=False)
    assert report["turns_record_held"]["sessions"] == 0
    assert sorted(r["id"] for r in report["short_turn_count"]["sample"]) == [
        "s-armed", "s-partial"]
    assert report["short_turn_count"]["all_short"] == 2
    assert report["short_turn_count"]["turns_missing"] == 8
    assert report["short_turn_count"]["turns_in_record"] is False


def test_the_capability_input_defaults_to_the_process_record_dsn(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """``turns_in_record=None`` reads the store from the configuration.

    The predicate is the capture writer's own capability question, reduced to
    the half an audit process can see. Both arms are pinned, and so is the
    failure direction: an unreadable configuration answers ``False`` — the
    pre-#10000 report — because a bucket that silently claimed "the record
    holds them" on a bad read would suppress a real shortfall.
    """
    import tortoise.record_writer as record_writer

    monkeypatch.setattr(record_writer, "record_dsn",
                        lambda: "postgresql://record@127.0.0.1:5432/record")
    armed = session_schema.audit_session_retrofit(_TwoShapeGraph())
    assert armed["short_turn_count"]["turns_in_record"] is True
    assert armed["turns_record_held"]["sessions"] == 1

    monkeypatch.setattr(record_writer, "record_dsn", lambda: None)
    unarmed = session_schema.audit_session_retrofit(_TwoShapeGraph())
    assert unarmed["short_turn_count"]["turns_in_record"] is False
    assert unarmed["turns_record_held"]["sessions"] == 0

    def _explode():
        raise RuntimeError("configuration unreadable")

    monkeypatch.setattr(record_writer, "record_dsn", _explode)
    unreadable = session_schema.audit_session_retrofit(_TwoShapeGraph())
    assert unreadable["short_turn_count"]["turns_in_record"] is False, (
        "an unreadable configuration must leave the report as it was, never "
        "claim the record holds the turns")
    assert unreadable["turns_record_held"]["sessions"] == 0


# ── the recorded decisions must have a real home ───────────────────────────

ONTOLOGY = REPO_ROOT / "docs/ONTOLOGY.md"
# The changelog prose moved out of `ONTOLOGY.md` into its sibling (#8745), so a
# `Changelog vX.Y` header is read from there — the model file no longer carries
# one, and pinning the header's home to `ONTOLOGY.md` would fail on a document
# that is correct.
ONTOLOGY_CHANGELOG = REPO_ROOT / "docs/ONTOLOGY-CHANGELOG.md"

# What the §4.3 approval-citation pin accepts as a comment id. The token must be
# ADJACENT to the digits: an earlier cut allowed up to 12 non-digits in between,
# which read the ISSUE number in the rejected shape below as a comment citation.
_CITATION_RE = re.compile(r"#issuecomment-(\d{6,})|comment(?:-id)?\s*[:`]?\s*(\d{6,})")
_REJECTED_CITATION_SHAPE = "See the discussion comments 118245"

# ⛔ ONE TABLE, THREE READERS (#12068): the denial VOCABULARY, the REGEX the arm
# matches with, and the FIXTURES that drive every row. A regex spelled beside a test
# that drives only its first alternative is how six of these eight rows went
# undriven in the first cut of this arm (measured: deleting them left the suite
# green). Each row is (label, fragment, sentence) — the sentence carries the denial
# AND an attributed quotation, so a row still refuses when the denial arm alone is
# removed, and `test_the_approval_arm_refuses_a_bare_or_
# disclaimed_citation` iterates this table rather than restating it.
#
# ⛔ THE DENIAL SCOPE IS THE WHOLE ENTRY, and an earlier cut of THIS comment said the
# opposite ("beside the id, not anywhere in the entry") while the arm already refused
# entry-wide — a comment that promised a leniency the code did not grant, which is the
# same class of false statement the pin exists to remove. A release that advertises
# the absence of the approval it owes does so in the entry, not in a sentence; the
# COST (an entry DISCLOSING a past state is refused, and must rephrase) is stated at
# the arm.
_APPROVAL_DENIAL_SHAPES: tuple[tuple[str, str, str], ...] = (
    ("the absence is stated outright", r"\bno\s+approval\b",
     "No approval is recorded anywhere, so comment {id}: *\"the clause stands as "
     "landed\"* is cited for it."),
    ("approval is negated", r"\bnot\s+approved\b",
     "The change was not approved, and comment {id}: *\"the clause stands as "
     "landed\"* is the closest relay."),
    ("approval was never given (past tense)",
     r"\bapproval\s+was\s+not\b",
     "The owner's approval was not on record; comment {id}: *\"the clause stands "
     "as landed\"* records only the wording."),
    ("approval is not on record (present tense)",
     r"\bapproval\s+is\s+not\b",
     "The owner's approval is not on record; comment {id}: *\"the clause stands as "
     "landed\"* records only the wording."),
    ("the owner never approved it", r"\bnever\s+approved\b",
     "The owner never approved this clause, so comment {id}: *\"the clause stands "
     "as landed\"* is not its home."),
    ("it landed without approval", r"\bwithout\s+approval\b",
     "The clause landed without approval; comment {id}: *\"the clause stands as "
     "landed\"* is cited for its wording only."),
    ("the clause is unapproved", r"\bunapproved\b",
     "The clause is unapproved, and comment {id}: *\"the clause stands as landed\"* "
     "is cited only for its wording."),
    # ⛔ ONE ROW PER ALTERNATIVE, because an alternative inside a shared fragment is a
    # shape no fixture can narrow: `(?:records?|states?|approves?)` driven by a sentence
    # that only ever says `records` was measured narrowable to `records?` with the suite
    # green, after which `states`/`approves` denials were ACCEPTED.
    ("the cited id is said not to record it", r"\bno\s+comment\b.{0,60}\brecords?\b",
     "no comment {id}: *\"the clause stands as landed\"* records it; the relay "
     "happened elsewhere."),
    ("the cited id is said not to state it", r"\bno\s+comment\b.{0,60}\bstates?\b",
     "no comment {id}: *\"the clause stands as landed\"* states it; the relay "
     "happened elsewhere."),
    ("the cited id is said not to approve it",
     r"\bno\s+comment\b.{0,60}\bapproves?\b",
     "no comment {id}: *\"the clause stands as landed\"* approves it; the relay "
     "happened elsewhere."),
    ("the cited id is called unrelated",
     r"\bcomment\b.{0,80}\bis\s+unrelated\b",
     "the only comment {id}: *\"the clause stands as landed\"* is unrelated."),
)
_APPROVAL_DENIAL_RE = re.compile(
    "|".join(fragment for _, fragment, _ in _APPROVAL_DENIAL_SHAPES), re.I)
# ⛔ THE VOCABULARY IS PINNED SEPARATELY, as (label, fragment) PAIRS, because both
# readers DERIVE from the table: deleting a row shrinks the regex AND the fixtures
# together, and NARROWING a fragment leaves its own fixture still refusing, so either
# edit would leave the suite green (both measured under #12068's review — deletion was
# caught by a labels-only pin, narrowing was not). A cut that really means to change a
# shape must edit this line too, which is a visible change under review rather than a
# silent narrowing. The SENTENCES stay out of the pin: their wording is a fixture, the
# fragment is the protection.
_EXPECTED_DENIAL_SHAPES: tuple[tuple[str, str], ...] = (
    ("the absence is stated outright", r"\bno\s+approval\b"),
    ("approval is negated", r"\bnot\s+approved\b"),
    ("approval was never given (past tense)", r"\bapproval\s+was\s+not\b"),
    ("approval is not on record (present tense)", r"\bapproval\s+is\s+not\b"),
    ("the owner never approved it", r"\bnever\s+approved\b"),
    ("it landed without approval", r"\bwithout\s+approval\b"),
    ("the clause is unapproved", r"\bunapproved\b"),
    ("the cited id is said not to record it",
     r"\bno\s+comment\b.{0,60}\brecords?\b"),
    ("the cited id is said not to state it",
     r"\bno\s+comment\b.{0,60}\bstates?\b"),
    ("the cited id is said not to approve it",
     r"\bno\s+comment\b.{0,60}\bapproves?\b"),
    ("the cited id is called unrelated",
     r"\bcomment\b.{0,80}\bis\s+unrelated\b"),
)
# ⛔ A QUOTATION ONLY DISCHARGES THE ARM WHEN IT IS ATTRIBUTED TO THE CITATION. The
# first cut accepted any quoted span within 400 characters, which a landing entry
# satisfies with a code token or a file path mentioned for another reason (measured:
# `The owner approved the change (comment <id>) that makes "superseded" mean
# replaced.` was green). So the introduction itself is pinned: the id, then an
# optional closing backtick, an introducer (`:`/`—`/`-`/`(`), optional emphasis, and
# the quotation. ⛔ THE FLOOR IS TWO THINGS: eight characters AND a run of four
# letters, because a character count alone admits `"        "` and `"........"` —
# measured, both discharged the arm, so "eight characters keeps `\"ok\"` out" was a
# claim the code did not enforce.
# ⛔ THE QUOTED SENTENCE IS NOT CAPPED IN LENGTH. The first cut matched against a
# 40-character slice, so the CLOSING quote had to fall inside it and any real
# approving sentence was refused (measured: a 37-character body was green only by
# luck and a 90-character one was red, with a message telling the lane to write
# exactly what it had written). The pattern is anchored immediately after the id, so
# no further bound is needed.
_ATTRIBUTED_QUOTE_RE = re.compile(
    r'^[`\']?\s*(?:[:—–(]|-)\s*[*_]{0,2}"(?=[^"\n]{8,}")[^"\n]*[A-Za-z]{4,}[^"\n]*"')
PLAN = REPO_ROOT / "docs/plans/2026-08-25-1714-memory-capture-onboarding.md"


def _session_section() -> str:
    """The §4.8 body, with the §5 heading as its exclusive end."""
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    start = ontology.index("### §4.8")
    return ontology[start:ontology.index("## §5.", start)]


def test_retrofit_verdicts_cite_documentation_homes_that_exist() -> None:
    """A ``recorded-honest-gap`` decision is only recorded if its home exists.

    The module's framing is that these three divergences are *recorded*
    decisions; pre-fix every ``where_recorded`` pointed at
    ``docs/ONTOLOGY.md §4.8`` and a plan ``#3554 amendment``, neither of which
    existed.
    """
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")
    assert "### §4.8" in ontology
    assert "#3554 amendment" in plan
    for name, verdict in session_schema.RETROFIT_VERDICTS.items():
        where = verdict["where_recorded"]
        assert "docs/ONTOLOGY.md §4.8" in where, name
        assert "§ '#3554 amendment'" in where, name


def _model_version(text: str) -> tuple[int, ...]:
    """``"3.30"`` → ``(3, 30)`` — compare model versions numerically, not as text."""
    return tuple(int(part) for part in text.split("."))


_BLOCK_VERSION_RE = re.compile(r"^>\s*\*\*Changelog v(\d+\.\d+)", re.M)


def _newest_changelog_version(changelog: str) -> str:
    """The version of the NEWEST block — the changelog is newest-first."""
    at = changelog.index("> **Changelog v3.")
    line = changelog[at:changelog.index("\n", at)]
    match = _BLOCK_VERSION_RE.match(line)
    assert match, f"unparseable newest changelog header: {line!r}"
    return match.group(1)


def _assert_declared_version_is_recorded(ontology: str, changelog: str) -> None:
    """The version the model document declares must BE a recorded release.

    ``docs/ONTOLOGY-CHANGELOG.md`` states its own rule — *"one entry per model
    change, newest first"* — and ``docs/ONTOLOGY.md``'s header relies on it for
    "the history of **what changed and why**". So the newest block and the
    declared version are the same fact seen twice, and a bump with no entry is
    an unrecorded model change.

    This is the relation the two removed arms ("§4.8's block == declared" and
    "§4.8's block is the newest") were reaching for. They pinned it to §4.8's
    bullet, which over-constrained it: any later release reddened a test about a
    section that had not moved. Keyed on the newest block instead, it holds
    whatever §4.8's bullet does, and the direction that matters — a version
    declared with no block recording it — is still excluded (see
    ``test_a_model_version_bump_with_no_changelog_block_fails``).
    """
    declared = re.search(r'^title: "Tortoise — Canonical Ontology v(\d+\.\d+)"',
                         ontology, re.M)
    assert declared, "docs/ONTOLOGY.md must declare its model version in frontmatter"
    newest = _newest_changelog_version(changelog)
    assert _model_version(newest) == _model_version(declared.group(1)), (
        f"docs/ONTOLOGY.md declares v{declared.group(1)} but the newest "
        f"ONTOLOGY-CHANGELOG.md block is v{newest} — a model version bump landed "
        "with no record of what changed")


def test_section_4_8_changelog_entry_sits_under_its_own_version_block() -> None:
    """The §4.8 changelog bullet belongs to the release that carries it.

    Pre-fix the bullet was the FIRST item of the ``v3.23 (2026-10-10 —
    issues #7813 + #7853)`` block, making that header's date and issue
    enumeration false and sending a reader looking up "when was §4.8 added"
    to the wrong version.

    The first fix moved it under ``v3.24`` and hardcoded that answer — but a new
    MODEL SECTION is a model change, so the block must be the release that
    carries the section, not the release #3554 happened to branch from. At that
    fix's head the document was v3.28 with v3.25/26/27/28 above v3.24: §4.8 sat
    four model versions old, the model version was never bumped, and the v3.24
    header enumerated an issue that could not have landed in it — the same
    misattribution one block up, now pinned as correct by a hardcoded ``v3.24``.

    The second fix asserted "the bullet sits under the NEWEST block, and that
    block's version is what the model document declares" — an OVER-CONSTRAINT:
    it made ANY later model release red, though the model is meant to keep
    evolving and the §4.8 bullet had not moved. A later release (v3.30, #8745,
    a §4.6-only change) failed it, which is not the misattribution it was
    reaching for. The relation it wanted is "the block carries the section's own
    release", so that is what is pinned — from facts already in the text, with
    no version literal: the bullet records its own landing date
    (``§4.8 (added by #3554, 2026-10-17)``) and the header records the block's
    date, so a bullet under ANY other block — older *or* newer — disagrees; and
    the block's version may not be NEWER than the model document declares
    itself. Pre-fix (the ``v3.23`` shape, the ``v3.24`` one, and a later block
    that copies the bullet) each fails; a later block that leaves the bullet
    alone (v3.30) does not.

    The third fix removed the two arms that pinned the DECLARED version to a
    block without putting the relation back: ``§4.8's block <= declared`` is
    satisfied by any later declared version, so a bump with **no changelog
    entry** — a state those arms made impossible and nothing else in the tree
    checks — went green. That relation is re-pinned here keyed on the NEWEST
    block (``_assert_declared_version_is_recorded``), which is where the
    changelog's own "one entry per model change, newest first" rule puts it.
    """
    changelog = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    bullet_at = changelog.index("§4.8 (added by #3554")
    header = changelog.rindex("> **Changelog v3.", 0, bullet_at)
    header_line = changelog[header:changelog.index("\n", header)]
    block_version = _BLOCK_VERSION_RE.match(header_line)
    assert block_version, f"unparseable changelog header: {header_line!r}"
    # The version the model document declares about itself (frontmatter title).
    declared = re.search(r'^title: "Tortoise — Canonical Ontology v(\d+\.\d+)"',
                         ontology, re.M)
    assert declared, "docs/ONTOLOGY.md must declare its model version in frontmatter"
    assert _model_version(block_version.group(1)) <= _model_version(declared.group(1)), (
        f"§4.8 is recorded under {header_line!r} — a block NEWER than the "
        f"v{declared.group(1)} this model document declares; a section cannot be "
        "recorded in a release that has not been published")
    assert "#3554" in header_line, (
        "the §4.8 block's header must name the issue whose section it records")
    # The RELATION: the block carrying the bullet is the release §4.8 landed in.
    bullet_line = changelog[bullet_at:changelog.index("\n", bullet_at)]
    landing = re.search(r"§4\.8 \(added by #3554, (\d{4}-\d{2}-\d{2})\)", bullet_line)
    block_date = re.search(r"\*\*Changelog v3\.\d+ \((\d{4}-\d{2}-\d{2})", header_line)
    assert landing and block_date, (
        f"both dates must be readable: bullet={bullet_line!r} header={header_line!r}")
    assert landing.group(1) == block_date.group(1), (
        f"§4.8 landed on {landing.group(1)} but sits under {header_line!r}, dated "
        f"{block_date.group(1)} — a model section belongs to the block that "
        "released it, not to whichever block happened to be newest when the "
        "bullet was written")
    # …and the version↔release relation, decoupled from where §4.8's bullet sits.
    _assert_declared_version_is_recorded(ontology, changelog)


def test_a_model_version_bump_with_no_changelog_block_fails() -> None:
    """Non-vacuity for the version↔release pairing, in both directions.

    The real tree is GREEN, so an arm that only runs against it proves nothing
    about the state it exists to exclude. This reproduces that state — the model
    document bumped to a version the changelog has no block for — and shows the
    pairing rejects it, while the arm the third rewrite used
    (``§4.8's block <= declared``) cannot see it at all. Then it records the
    bumped version and shows the same document goes green, so the arm reads the
    relation and not a version literal.
    """
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    changelog = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")
    _assert_declared_version_is_recorded(ontology, changelog)

    declared = re.search(r'^title: "Tortoise — Canonical Ontology v(\d+\.\d+)"',
                         ontology, re.M)
    assert declared, "docs/ONTOLOGY.md must declare its model version in frontmatter"
    current = declared.group(1)
    major, minor = current.split(".")
    bumped_version = f"{major}.{int(minor) + 1}"
    bumped = ontology.replace(f"Canonical Ontology v{current}",
                              f"Canonical Ontology v{bumped_version}", 1)
    assert bumped != ontology, "the declared version must be replaceable in the title"

    # The arm the third rewrite left in place cannot see the regression: §4.8's
    # block is v3.29 and stays there, so `block <= declared` holds for any bump.
    bullet_at = changelog.index("§4.8 (added by #3554")
    header = changelog.rindex("> **Changelog v3.", 0, bullet_at)
    block_version = _BLOCK_VERSION_RE.match(changelog[header:changelog.index("\n", header)])
    assert block_version, "§4.8's block header must be readable"
    assert _model_version(block_version.group(1)) <= _model_version(bumped_version), (
        "this test's premise is that the §4.8-only bound accepts a bump with no record")

    with pytest.raises(AssertionError, match="no record of what changed"):
        _assert_declared_version_is_recorded(bumped, changelog)

    newest_at = changelog.index("> **Changelog v3.")
    recorded = (changelog[:newest_at]
                + f"> **Changelog v{bumped_version} (2199-01-01 — issue #0 — synthetic):**\n>\n"
                + changelog[newest_at:])
    _assert_declared_version_is_recorded(bumped, recorded)


# ── the newest block's §4.3 quote must BE the landed §4.3 sentence ──────

OBJECT_CLAUSE_START = "**Core Object types accept"
OBJECT_CLAUSE_END = (
    "Pack-declared kinds validate against their own declared words.**")


def _landed_object_lifecycle_clause(ontology: str) -> str:
    """The §4.3 `status` row's bold lifecycle clause, as the model states it."""
    for line in ontology.splitlines():
        if line.startswith("| `status` |") and OBJECT_CLAUSE_START in line:
            start = line.index(OBJECT_CLAUSE_START)
            end = line.index(OBJECT_CLAUSE_END, start) + len(OBJECT_CLAUSE_END)
            return line[start:end]
    raise AssertionError(
        "docs/ONTOLOGY.md §4.3's `status` row carries no lifecycle clause — the "
        "sentence this file's vocabulary is built from is missing")


def _newest_block_body(changelog: str) -> str:
    """The newest block's text, FROM its header line to the next block's header.

    ⛔ THE EXTENT IS ``_block_bodies(changelog)[0]`` EXACTLY, header line included.
    An earlier cut sliced from AFTER the header line (``changelog[index("\n",
    start):]``), which its docstring then described as "from its header" — a
    docstring disagreeing with its own return value, and a blind spot in the pin
    that reads this text to find a citation: a comment id written in the block's
    HEADER line was invisible to it.

    Measured at head ``7e6baffc5d``: this returns 11375 characters, equal to
    ``len(_block_bodies(changelog)[0])``; the after-the-header cut returned 11269
    and the two were not equal. No block currently writes a citation in its header
    (measured: 0 of 39 headers carry one), so the wider extent changes no verdict
    today — it closes the blind spot rather than reacting to a live miscount.
    """
    first = _BLOCK_VERSION_RE.search(changelog)
    assert first, "the changelog's newest block header must be readable"
    header_end = changelog.index("\n", first.start())
    following = _BLOCK_VERSION_RE.search(changelog, header_end)
    return (changelog[first.start():following.start()] if following
            else changelog[first.start():])


def _quoted_clause(block: str) -> str | None:
    """The block's `> > ` quote of the §4.3 lifecycle clause, normalised.

    Markdown line wrapping is not part of the quoted text, so the quote's lines
    are joined with single spaces — the comparison is of the SENTENCE.
    """
    lines = block.splitlines()
    starts = [i for i, line in enumerate(lines)
              if line.startswith("> > ") and OBJECT_CLAUSE_START in line]
    if not starts:
        return None
    quote: list[str] = []
    i = starts[0]
    while i < len(lines) and lines[i].startswith("> > "):
        quote.append(lines[i][4:])
        i += 1
    return " ".join(quote)


def _block_bodies(changelog: str) -> list[str]:
    """Every RELEASE block's text, newest first (the file's own order).

    ``^`` is load-bearing: a block's prose can QUOTE an older header
    (``> > **Changelog v3.7 …`` inside the v3.2 entry), and an unanchored
    pattern counts that quote as a release — measured at this head: 40 pieces
    for 39 blocks, one of them a fragment starting at the quoted header.
    """
    starts = [m.start() for m in _BLOCK_VERSION_RE.finditer(changelog)]
    # `pairwise` over the spans plus the end offset — the spans are DELIBERATELY
    # one shorter than the pairs (B905's `strict=` would be a lie here), and it
    # replaces the `+ [len(changelog)]` concatenation RUF005 rejects.
    return [changelog[a:b]
            for a, b in itertools.pairwise([*starts, len(changelog)])]


# ⛔ ONE ORDINAL RECOGNISER FOR BOTH SITES — the module-wide sweep and the pin's own
# read of the SCOPE paragraph — accepting `file's` in EITHER position and case-blind.
# ⛔ THE PATTERNS LIVE HERE SO BOTH READERS CAN NAME THEM: the pin parses with them,
# and `test_every_declared_sweep_arm_refuses_a_stale_restatement` drives the
# uniqueness scan with them. Re-spelling one in the test would create a SECOND
# literal copy, which the scan itself refuses.
_CITATION_PAIR_PATTERN = r"\*\*(\d+) of (\d+)\*\* blocks cite a comment id"
_CITATION_VERSION_PATTERN = r"citing versions: (v[^—]+)"
_CITATION_COMPLEMENT_PATTERN = r"\*\*(\d+) of (\d+)\*\* cite none"
_D2_ORDINAL_PATTERN = (r"the (?:file's )?(first|second) of the (?:file's )?"
                       r"two v3\.33 entries")


def _citation_ids(text: str) -> list[str]:
    """Every comment id `text` cites, in ANY form `_CITATION_RE` accepts."""
    return [permalink or inline for permalink, inline in _CITATION_RE.findall(text)]


def _refuse_a_duplicated_statement(text: str, pattern: str, label: str) -> None:
    """Refuse a patterned statement that appears more than once in `text`.

    ⛔ A HELPER SO THE ARM IS REACHABLE FROM SYNTHETIC TEXT: inline, deleting it left
    the file green (measured) while the comment above it still promised the scan.
    """
    found = len(re.findall(pattern, text))
    assert found == 1, (
        f"{label} must appear exactly ONCE (found {found}) — a second patterned copy "
        "can shadow the one parsed below, and a bold duplicate whose numbers match "
        "would pass the value sweep, so the count is what refuses both")


def _refuse_unresolvable_evidence(prose: str, block_texts: list[str], *,
                                  carrier: str | None = None) -> None:
    """Refuse a docstring permalink that no block can resolve — or that `carrier` lacks.

    ⛔ A HELPER SO BOTH PERMALINK ARMS ARE REACHABLE FROM SYNTHETIC TEXT. Inline, they
    were deletable in silence: measured at ``6dca05bd``, deleting either left the
    file at 54 passed while the paragraph above still promised the sweep. The two
    arms still differ — by the SCOPE OF ATTRIBUTION (`carrier`), never by the
    spelling of the id.
    """
    resolvable = {identifier for block in block_texts
                  for identifier in _citation_ids(block)}
    cited = set(re.findall(r"#issuecomment-(\d{6,})", prose))
    assert cited <= resolvable, (
        f"these docstrings cite permalinks {sorted(cited - resolvable)}, which no "
        "changelog block carries in any accepted form — an id a reader cannot resolve "
        "to some block's evidence is the defect this pin exists to stop")
    if carrier is not None:
        carried = set(_citation_ids(carrier))
        assert cited <= carried, (
            f"this paragraph cites {sorted(cited - carried)}, but the block it "
            f"points at carries {sorted(carried)}")


def _unfold_block(block: str) -> str:
    """The block with its markdown wrapping removed and its quote prefixes stripped.

    ⛔ UNFOLDED, because a soft wrap is not a semantic boundary. The arm is
    wrap-SENSITIVE in two places — a denial and its citation can be split across a
    wrap, and the quoted sentence can be wrapped MID-QUOTATION — and folding means a
    re-flow of an entry cannot change what this arm decides. Measured at this head,
    the landed entry's accepted citation and its quotation DO share a physical line,
    so unfolding changes no current verdict: it removes a re-wrap false BLOCK, and
    the wrap is driven from synthetic text below rather than assumed away.
    """
    return " ".join(re.sub(r"^>\s?", "", line).strip()
                    for line in block.splitlines())


def _top_level_calls_of(source: str) -> set[str]:
    """The functions a `def` at the top of `source` calls as a BARE STATEMENT.

    ⛔ A CALL NODE, NOT A SUBSTRING. A substring search for a call name is satisfied
    by a comment or a string, and it is satisfied by a call nested in an `if False:`
    left behind when a lane disables an arm — measured at #12068's review: deleting
    the call and leaving `# … used to run here` in the body left both tests green, and
    an `if False:` wrapper kept the name in the source too. Only a call that executes
    on every path is a wiring, and that is what this reads.
    """
    tree = ast.parse(textwrap.dedent(source))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef))
    return {statement.value.func.id
            for statement in function.body
            if isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)}


_MARK_CHARS_RE = re.compile(r"[*_`'\"]")


def _without_quotations(text: str, citations: list[re.Match[str]]) -> str:
    """`text` with each ATTRIBUTED quotation collapsed to an empty pair of quotes.

    ⛔ ONLY THE QUOTATION THAT BELONGS TO A CITATION, and the first cut of this helper
    collapsed EVERY quoted span — which erased a denial whose pivotal word the entry
    had put in its own quotation marks (`No "approval" is recorded anywhere`) and
    left the pin green over it (measured under #12068's review). The quoted words of
    the cited comment are the comment's statement, not the entry's; quotation marks
    the entry uses for anything else are the entry's own prose.

    Collapsing the attributed span is also what makes the two rows with an internal
    character bound (`no comment … records`, `comment … is unrelated`) reachable when
    a REAL quotation sits between the two halves — a 76-character quotation carried
    both shapes past those bounds, and the pin reported OK (measured).
    """
    out: list[str] = []
    last = 0
    for citation in citations:
        span = _ATTRIBUTED_QUOTE_RE.match(text[citation.end():])
        if span is None:
            continue
        out.append(text[last:citation.end()])
        out.append('""')
        last = citation.end() + span.end()
    out.append(text[last:])
    return "".join(out)


# The spans `_collapse_all_quotations` removes: every way this document marks words
# as QUOTED or as a CODE SPAN — both quote conventions, backticks, and bold/italic —
# so an interposed one contributes no filler to a row's internal bound.
# ⛔ THE `_` AND `*` ALTERNATIVES ARE BOUNDED BY NON-WORD CHARS so an identifier
# (`superseded_by`) is not read as emphasis.
_MARKED_SPAN_RE = re.compile(
    r'\*\*[^*\n]+\*\*'          # **bold**
    r'|(?<![A-Za-z0-9])__[^_\n]+__(?![A-Za-z0-9])'   # __bold__
    r'|`[^`\n]+`'               # `code`
    r'|"[^"\n]*"'              # "double quoted"
    r"|'[^'\n]*'"              # 'single quoted'
    r'|\*(?!\*)[^*\n]+\*'       # *italic*
    r'|(?<![A-Za-z0-9])_[^_\n]+_(?![A-Za-z0-9])')   # _italic_


def _collapse_all_quotations(text: str) -> str:
    """Every double-quoted span collapsed to an empty pair of quotes.

    ⛔ THE SECOND VIEW, and it exists for one measured bypass: rows 7 and 8 carry an
    internal width bound, and an interposed quotation the ENTRY owns (`… — the annex
    "archived does not close policy B" — records the approval`) fills that bound with
    characters no fixture reaches. Collapsing every quotation here means such filler
    contributes nothing, so the two halves are as close as they are in plain prose.
    ⛔ IT CANNOT REPLACE THE ATTRIBUTED-ONLY VIEW: collapsing everything erases a
    denial whose pivotal word the entry itself quoted (`No "approval" is recorded`),
    which is why the arm searches BOTH and either may refuse.
    ⛔ IT COLLAPSES BOTH QUOTE CONVENTIONS, BACKTICKS AND EMPHASIS, because an earlier
    cut collapsed only `"…"` and an interposed span written the document's other way
    (`*archived does not close policy B*`, `'…'`, `` `…` ``) carried a disclaimer past
    the same bound (all measured). See `_MARKED_SPAN_RE`.
    """
    return _MARKED_SPAN_RE.sub('""', text)


def _fuse_marks(text: str) -> str:
    """`text` with marking characters DELETED, so a mark inside a word cannot split it.

    ⛔ THE COMPANION OF `_deemphasise`, NOT A REPLACEMENT FOR IT, and the pair is why
    the vocabulary is matched on FOUR views. Replacing a mark with a SPACE keeps
    `No**approval**` adjacent but splits a mark written INSIDE a token
    (`un**approved**` → `un approved`), while DELETING it does the reverse
    (`No**approval**` → `Noapproval`, `un**approved**` → `unapproved`). Both forms are
    this document's own idiom, and each was measured accepted while only the other
    view existed.
    """
    return _MARK_CHARS_RE.sub("", text)


def _denial_views(unfolded: str, citations: list[re.Match[str]]) -> list[str]:
    """Every view the denial vocabulary is matched on — four, and all of them needed.

    Two QUESTION scopes (the attributed quotation collapsed so a quoted statement is
    the comment's, then EVERY marked span collapsed so interposed filler contributes
    nothing to a row's internal bound) crossed with two MARK treatments (marks spaced,
    marks deleted, per `_deemphasise` / `_fuse_marks`). Any one view alone was
    measured bypassable; the arm refuses if ANY view refuses.
    """
    return [treatment(base)
            for base in (_without_quotations(unfolded, citations),
                         _collapse_all_quotations(unfolded))
            for treatment in (_deemphasise, _fuse_marks)]


def _deemphasise(text: str) -> str:
    """`text` with markdown emphasis markers and code ticks replaced by spaces.

    ⛔ THE DENIAL VOCABULARY IS MATCHED ON THIS, NOT ON THE RAW ENTRY. This document
    bolds mid-phrase as its own idiom (`docs/ONTOLOGY-CHANGELOG.md` carries dozens of
    mid-sentence `**…**` insertions), so a vocabulary that matched only plain prose
    refused nothing real: `No **approval** is recorded anywhere` was ACCEPTED, and so
    were `not *approved*` and ``without `approval``` (all measured under #12068's
    review). Replaced by a SPACE rather than deleted, so `no**approval**` cannot fuse
    into a token that neither the pattern nor a reader would recognise.
    ⛔ QUOTATION MARKS ARE IN THE CLASS TOO, BOTH CONVENTIONS: an entry's own
    quotation marks around the pivotal word (`No "approval" is recorded anywhere`,
    `No 'approval' is recorded anywhere`) are formatting as well, and leaving them in
    broke the adjacency the vocabulary needs — the single-quote form is this
    document's own idiom and was measured green until it was added here.
    """
    return re.sub(r"[*_`'\"]", " ", text)


def _refuse_a_bare_or_disclaimed_citation(block: str) -> None:
    """The cited comment's WORDS must be ATTRIBUTED to it, and not disclaimed.

    ⛔ THE TWO SHAPES #12068 MEASURED AS PASSING, and why a form check is not enough:
    a release could advertise the ABSENCE of the approval it owes — a denial beside
    the citation — or cite an id only to disclaim it, and the enumerated-forms arm
    accepted both. Each is refused here under its own message, so a red tells the lane
    which half failed rather than that "the entry is bad".

    ⛔ THE DENIAL SCOPE IS THE WHOLE ENTRY, and two narrower cuts were each measured
    bypassable before it: breaking the window on `; ` or `: ` let a denial escape by
    punctuation alone, and breaking it only on `. ` let the same denial escape into
    the PREVIOUS or the FOLLOWING sentence (both measured green). A release
    advertising the absence of the approval it owes is a property of the ENTRY, not of
    a sentence inside it.
    ⛔ WHAT THAT COSTS, stated as the code performs it: an entry that DISCLOSES a past
    state in one of the enumerated words (*"an earlier cut said no approval was
    recorded"*) is refused too; the remedy is to rephrase the disclosure or keep it in
    the issue. Measured at this head, the landed entry carries none of them, so the
    cost is a possible future false block — the safer error than the false PASS this
    arm exists to close.

    ⛔ THE RESIDUAL OF THE LEXICAL DENIAL DETECTOR, stated because it is the last one
    and it cannot be closed by a bigger pattern: two of the eight rows (the ones that
    say the cited id does not record the approval) carry an internal bound, and a
    disclaimer PADDED WITH PLAIN PROSE between the two halves
    (`no comment <id>: "…", and the annex on policy B sets out the retirement route
    in full, records the signoff`) is accepted. Everything MARKED is refused — both
    quote conventions, backticks, bold and italic, before or after the citation —
    because a marked span is collapsed before the bound is measured, and the fixtures
    drive one of each. What remains needs no pattern: an author who writes that
    sentence has written the disclaimer, and a reviewer reads it.

    ⛔ ATTRIBUTED, not merely quoted. A quotation anywhere near the id is not
    evidence for it: an earlier cut accepted one, and `The owner approved the change
    (comment <id>) that makes "superseded" mean replaced.` was green — the arm
    discharged by a code token quoted for another reason (measured). The
    introduction the entry must write is `comment <id>: "…"`.

    ⛔ THE RESIDUAL IS STATED, NOT PAPERED OVER: this arm reads TWO FILES, so a
    release can still pair a REAL, resolvable id with a FABRICATED quotation in
    exactly the accepted shape. Closing that needs the comment's own text, which is a
    reviewer step (this suite is static by design — see
    `tests/test_extractor_v4_citation_rot.py` on its own citations), and it is
    recorded in #12068.
    """
    unfolded = _unfold_block(block)
    citations = list(_CITATION_RE.finditer(unfolded))
    assert citations, (
        "the block carries no citation for this arm to read — the enumerated-forms "
        "check above must refuse it first")
    denial = next((match for match in
                   (_APPROVAL_DENIAL_RE.search(view)
                    for view in _denial_views(unfolded, citations))
                   if match is not None), None)
    assert denial is None, (
        "the entry DISCLAIMS the approval rather than offering it as evidence: "
        f"{unfolded[max(0, denial.start() - 120):denial.end() + 120]!r}\n  what "
        "failed: the release states, in one of the enumerated forms, that the "
        "approval is absent or that the cited id does not record it\n  what to do: "
        "cite the comment that RECORDS the owner's approval and quote its words — "
        "and if this entry is disclosing a past state, rephrase the disclosure (a "
        "release that advertises the absence of the approval it owes is the shape "
        "this arm exists to refuse, #12068)")
    assert any(_ATTRIBUTED_QUOTE_RE.match(unfolded[match.end():])
               for match in citations), (
        "no citation in the block INTRODUCES the cited comment's own words, so the "
        "entry names a place to look without recording what was found there\n  what "
        "failed: the comment id is bare, or the quotation beside it is attributed to "
        "nothing\n  what to do: write `comment <id>: \"the approving sentence\"` — the "
        "id, an introducer, then the words a reader can check against the comment")


def _assert_no_stale_citation_claim(text: str, blocks: list[str], citing: list[str],
                                    *, lands_first: bool) -> list[str]:
    """Refuse every STALE restatement of this changelog's citation measurement.

    ⛔ WHY THE ARMS LIVE HERE. The double handful of sentences around this pin
    enumerate what the sweep reads, and an INLINE arm cannot be checked for the shape
    it claims: measured at head ``134495ba``, deleting the bold-pair arm (529
    characters) or the complement arm (263) left the file at 54 passed, and the same
    was still true at ``6dca05bd`` — the prose went on promising checks that no
    longer ran, which is finding 1's class applied to the pin's own disclosure. As a
    function of its TEXT, every arm is driven from synthetic input by
    ``test_every_declared_sweep_arm_refuses_a_stale_restatement``.

    THREE ARMS, each refusing one shape:

    1. a BOLD count that RESTATES this measurement — its denominator is the number of
       blocks, or the word ``blocks`` follows it (so a stale copy carrying a stale
       denominator is still caught). ⛔ WHAT THIS COSTS, stated as the code performs
       it: a bold count about something ELSE that is also counted in blocks-units (a
       header count out of the same total) is REFUSED, because no reader can tell it
       from a restatement — state such a count un-bolded. A bold pair counted in
       other units is not read at all, and is asserted to pass.
    2. ``the N blocks that cite nothing`` — the complement, restated in prose.
    3. an ordinal sentence naming ``D2 — A``, recognised by ``_D2_ORDINAL_PATTERN``
       (the same pattern the pin parses the SCOPE paragraph with). ⛔ WHAT THIS COSTS,
       as the code performs it: the sentence is the unit, so a text that names
       ``D2 — A`` AND an ordinal in one sentence is read as calling the lander that
       ordinal — a true remark about the OTHER v3.33 entry that also names the lander
       in the same sentence is REFUSED (measured: a true sentence naming the lander
       and that other entry's ordinal reds here). Write such a remark as its own
       sentence, without ``D2 — A``. The anchoring buys the case that
       actually happens — a sentence about the other entry ALONE — and the message
       says which of the two it saw, so the next lane is not told what its text said.

    Returns the ordinals it saw, so the caller can require that the module says which
    of the two entries lands the block at all (a prose requirement, not a verdict).
    """
    measured_none = len(blocks) - len(citing)
    allowed_bold = {(len(citing), len(blocks)), (measured_none, len(blocks))}
    for match in re.finditer(r"\*\*(\d+) of (\d+)\*\*", text):
        seen = (int(match.group(1)), int(match.group(2)))
        if seen[1] != len(blocks) and not re.match(
                r"\s+blocks?\b", text[match.end():]):
            continue
        assert seen in allowed_bold, (
            f"this text states **{seen[0]} of {seen[1]}** somewhere; the detector "
            f"measures {len(citing)} of {len(blocks)} citing a comment id and "
            f"{measured_none} of {len(blocks)} citing nothing. A count that "
            "restates this measurement must be one of those two — a stale copy "
            "beside the checked one is the defect finding 1 was about")
    for seen in re.findall(r"the (\d+) blocks? that cite nothing", text):
        assert int(seen) == measured_none, (
            f"this text restates the complement as 'the {seen} blocks that cite "
            f"nothing'; measured {measured_none}")
    ordinals = [word for sentence in re.split(r"(?<=[.)])\s+", text)
                if "D2 — A" in sentence
                for word in re.findall(_D2_ORDINAL_PATTERN, sentence, re.I)]
    for word in ordinals:
        assert (word.lower() == "first") == lands_first, (
            "a sentence here names both `D2 — A` and the "
            f"{word.lower()} of the two v3.33 entries, so the sweep reads it as "
            f"calling the D2 block the {word.lower()}; measured, it is the "
            f"{'first' if lands_first else 'second'}. If the sentence is making a "
            "different point, move the ordinal out of the `D2 — A` sentence.")
    return ordinals


def test_block_bodies_degrades_to_empty_without_a_version_header() -> None:
    """A changelog with NO version header has no blocks — and must not raise.

    `_block_bodies` walks adjacent pairs of `_BLOCK_VERSION_RE` matches plus the end
    offset. With no header there is exactly ONE offset (`len(changelog)`), so
    `itertools.pairwise` yields no pairs and `[]` falls out by construction. That is
    the correct reading of "every release block's text" for a file that has none.

    ⛔ PINNED BECAUSE THE OBVIOUS REFACTOR BREAKS IT SILENTLY. Pairing the spans against
    a separate `ends` list and ASSERTING the pairing (`zip(..., strict=True)`) raises
    `ValueError` on this input instead of returning no blocks — measured on an
    implementation that passed every other test in this module (51 green, 1 red: this
    one). The empty-changelog case is the only input that distinguishes the two, so
    without this pin that refactor is free to turn "no releases" into a crash.
    """
    assert _block_bodies("") == []
    assert _block_bodies("prose\nwith no version header\n") == []


def test_the_newest_block_body_starts_at_its_header_line() -> None:
    """The helper's documented extent IS what it returns: `_block_bodies(...)[0]`.

    ⛔ A DOCSTRING-ONLY FIX WOULD DRIFT BACK, and the gap was not cosmetic: this
    text is what the sibling pin searches for a comment id, so a citation written
    in the block's HEADER line was invisible to it. The defect was a docstring
    saying "from its header" over a slice that began AFTER that line — measured at
    head ``7e6baffc5d``: 11269 characters against 11375 for
    ``_block_bodies(...)[0]``, and the two were not equal. Equality (header
    included) is the contract below; nothing else in the file would have caught a
    revert, because 0 of 39 headers carry a citation at this head, so every
    citation verdict is unchanged either way.
    """
    changelog = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")
    body = _newest_block_body(changelog)
    blocks = _block_bodies(changelog)
    assert body == blocks[0], (
        "the newest block's extent must be exactly `_block_bodies(...)[0]`, its "
        f"header line included: got {len(body)} chars against {len(blocks[0])}")
    # ⛔ THE HEADER DEFINITION'S OWN PROPERTY. `body == blocks[0]` above already
    # pins the extent (`blocks` is derived from `_BLOCK_VERSION_RE.finditer`), so an
    # assertion that `body` matches that regex here could never fail. What is worth
    # pinning is the DEFINITION's own contract — that it reads a ``v4.x`` header and
    # a ``>`` with extra spacing — so that is asserted directly, and nowhere else in
    # this file is it pinned; tighten the regex (to ``v3\.``, or drop ``\s*``) and
    # this reds.
    assert _BLOCK_VERSION_RE.match(">  **Changelog v4.12 (2099-01-01 — synthetic):**"), (
        "`_BLOCK_VERSION_RE` must read any release header this file can carry: a "
        "major version above 3, and a `>` followed by extra spacing")
    # ⛔ THE JUSTIFICATION FOR THE WIDER EXTENT, ASSERTED. "No verdict moves today"
    # rests entirely on no block writing a citation in its HEADER line — the text
    # the old cut dropped. Stated twice in prose and checked nowhere, it could
    # become false while the extent pin stayed green.
    assert sum(1 for block in blocks
               if _CITATION_RE.search(block.split("\n", 1)[0])) == 0, (
        "a block's HEADER line now carries a comment citation, so the `0 of 39 "
        "headers cite` justification the wider extent rests on is FALSE — restate "
        "that claim. (The citing COUNTS are measured live by the citation pin, so a "
        "header citation on an already-citing block does not move them, and this "
        "guard is not their protector.)")


def test_the_lifecycle_clause_extraction_ignores_the_rest_of_the_row() -> None:
    """⛔ THE RESIDUAL THE SIBLING PIN'S NAME NOW STATES, pinned as behaviour.

    ``_landed_object_lifecycle_clause`` extracts ONE span of ONE cell — the bold
    clause from ``OBJECT_CLAUSE_START`` to ``OBJECT_CLAUSE_END`` — so rewriting
    the same ``status`` cell's OPENING SENTENCE, or another §4.3 row
    (``supersededBy``), leaves the comparison byte-identical and both pins green.
    That is stated in the sibling test's SCOPE paragraph as residual rather than
    claimed as covered (#9974 owns the opening sentence, which the v3.34 block
    itself records as WRONG about the code).

    This test exists so the extraction span cannot be read as wider than it is,
    and so WIDENING it is a deliberate, argued change: the day it grows, this
    test fails and forces the docstring, the test name and the false-block
    surface to move together.
    """
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    landed = _landed_object_lifecycle_clause(ontology)
    status_line = next(
        line for line in ontology.splitlines()
        if line.startswith("| `status` |") and OBJECT_CLAUSE_START in line)
    # ⛔ THE SPAN ITSELF, PINNED EXACTLY. The mutation arms below probe only the
    # two shapes they name (the cell's opening sentence, the `supersededBy` row);
    # measured, 5 of 7 growth shapes of this helper were UNCAUGHT by them — an END
    # moved to end-of-line, or `supersededAt` / `validFrom` / `createdAt` /
    # `objectKind` spliced onto the value, all stayed green. Comparing the value to
    # the span computed here makes any growth red this test.
    #
    # ⛔ AND THE ENDPOINTS ARE PINNED AS LITERALS, because the span below is
    # computed from `OBJECT_CLAUSE_START`/`OBJECT_CLAUSE_END` — the very constants
    # the helper reads — so MOVING an endpoint (the widening the SCOPE paragraph
    # names, e.g. `START` pulled back over the cell's opening sentence) would move
    # both sides together and leave this green (measured: 4 such edits, all green
    # before this pin). The literals are the endpoints the SCOPE paragraph already
    # quotes verbatim, so a constant edit now has to argue with both sentences.
    assert OBJECT_CLAUSE_START == "**Core Object types accept", (
        "the compared span's start moved — the SCOPE paragraph and this file's "
        "residual claim are written against this endpoint")
    assert OBJECT_CLAUSE_END == (
        "Pack-declared kinds validate against their own declared words.**"), (
        "the compared span's end moved — the SCOPE paragraph and this file's "
        "residual claim are written against this endpoint")
    clause_at = status_line.index(OBJECT_CLAUSE_START)
    exact = status_line[
        clause_at:status_line.index(OBJECT_CLAUSE_END, clause_at) + len(OBJECT_CLAUSE_END)]
    assert landed == exact, (
        "the extraction span is no longer exactly OBJECT_CLAUSE_START.."
        f"OBJECT_CLAUSE_END: {len(landed)} chars against {len(exact)} — the "
        "SCOPE paragraph, this file's residual pins and the sibling pin's "
        "false-block surface must move together with it")
    # A span that left the row would have been caught by the equality above (it is
    # a `splitlines()` slice), so no separate newline guard is asserted here.
    opening = "| `status` | string | — | `pav:status` | "
    assert opening in status_line, (
        "the §4.3 `status` row's shape moved — re-read the extraction span")
    # ⛔ THESE TWO UNIQUENESS GUARDS CAN ACTUALLY FAIL, which is why they are the
    # ones asserted: the mutations below replace exactly ONE occurrence, so a
    # duplicated cell prefix or a duplicated row would make them edit the wrong
    # text and the pin would pass while testing nothing.
    assert status_line.count(opening) == 1
    assert ontology.count(status_line) == 1
    mutated_row = status_line.replace(
        opening, opening + "REWRITTEN OPENING SENTENCE. ", 1)
    assert _landed_object_lifecycle_clause(ontology.replace(status_line, mutated_row, 1)) \
        == landed, (
        "the extraction span now sees the cell's opening sentence — the SCOPE "
        "paragraph and test name say it compares only the bold clause")
    successor_row = next(
        (line for line in ontology.splitlines()
         if line.startswith("| `supersededBy` |")), None)
    # ⛔ A SKIP, NOT A SILENT PASS. The row is present at this head (measured), so
    # this arm is live; the day it is renamed or removed, the docstring's claim
    # about it must stop being reported as verified — a silent `if` would let the
    # covered half of the claim vanish while the suite stays green.
    if successor_row is None:
        pytest.skip("§4.3 `supersededBy` row absent — that residual is UNVERIFIED")
    successor_prefix = "| `supersededBy` | string | — | — | "
    assert successor_prefix in successor_row and ontology.count(successor_row) == 1, (
        "the §4.3 `supersededBy` row's shape or uniqueness moved — the mutation "
        "below would otherwise rewrite text it does not target")
    mutated = ontology.replace(
        successor_row,
        successor_row.replace(successor_prefix, successor_prefix + "REWRITTEN. ", 1),
        1)
    assert _landed_object_lifecycle_clause(mutated) == landed, (
        "the extraction span now reads another §4.3 row")


def test_the_4_3_status_lifecycle_clause_must_be_quoted_verbatim_by_some_changelog_block() -> None:
    """A changelog that quotes a sentence the model does not carry is a false record.

    ``docs/ONTOLOGY.md:39`` and ``ONTOLOGY-CHANGELOG.md:32`` both require
    *explicit approval of the exact changes*, and the approval is of **exact
    text** — so any block that quotes the §4.3 lifecycle sentence must quote the
    sentence the model carries, and a release that CHANGES that sentence must
    quote it at all.

    Measured at head ``6057b6d4``: the v3.34 block still quoted the
    PRE-restatement wording ("At the write door `superseded` requires the
    successor …") while §4.3 carried the definition form. It was verbatim before
    that commit and the commit updated only one of the two copies, which is how
    an approved artifact came to disagree with itself. Nothing else in the tree
    compares them.

    ⛔ SCOPE OF THE CLAIM — read this before trusting the name. The COMPARED TEXT
    is one span of one cell: ``_landed_object_lifecycle_clause`` extracts the §4.3
    ``status`` row from ``OBJECT_CLAUSE_START`` (*"**Core Object types accept"*)
    to ``OBJECT_CLAUSE_END`` (*"Pack-declared kinds validate against their own
    declared words.**"*). It is NOT "§4.3 may not change": every other cell of
    that same row, and every other row of §4.3, is INVISIBLE to this comparison —
    a lane can rewrite the cell's opening sentence or the ``supersededBy`` row and
    this pin stays green, while ``docs/ONTOLOGY.md:39``'s CRITICAL RULE requires
    approval of *every* exact change. Those are stated as residual rather than
    claimed as covered; the same cell's opening sentence is in fact the one the
    v3.34 block calls *"pre-change build record and already recorded as WRONG
    about the code"* (parked on #9974), so pinning it verbatim is its own
    approval question, not a free widening. (The name said "A 4.3 CHANGE" while
    the code compared this span — a reader was told the gate covered more than it
    does, which is the class of claim this file exists to remove.)

    The second, weaker scope limit is unchanged: the comparison target is the
    newest block that quotes the clause AT ALL, not "the release that changed it
    quotes it" — so a lane could change §4.3 and paste
    the new sentence into an OLDER block's quote and this pin would pass
    (measured: new text into v3.34's quote plus a quoteless v3.35 → both pins
    green). Tying the quote to the version that actually made the change needs
    the previous release's clause, which the file does not carry; the residual
    is stated rather than papered over, and the cheap half — "the model and its
    record disagree" — is fully covered.

    ⛔ THE RULE IS "MAY NOT CHANGE WITHOUT A BLOCK RECORDING IT", NOT "EVERY
    BLOCK MUST QUOTE". An earlier cut of this pin demanded a §4.3 quote in the
    NEWEST block unconditionally, which reds the next release that touches §4.5
    or §4.6 and teaches the lane to paste a §4.3 quote into an entry that did
    not change §4.3 — a changelog recording a change it did not make. The landed
    clause is instead compared against the clause of the most recent block that
    DOES quote it: equal means §4.3 did not move this release (nothing to pin),
    different means it moved and this release must be the one recording the new
    text. Absence is therefore never a silent pass — a change with no quote
    fails, and there is no state in which both sides go uncompared.
    """
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    blocks = _block_bodies(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    landed = _landed_object_lifecycle_clause(ontology)
    quoted_pairs = [(b, q) for b in blocks if (q := _quoted_clause(b)) is not None]
    assert quoted_pairs, (
        "no ONTOLOGY-CHANGELOG.md block quotes the §4.3 lifecycle clause, so "
        "nothing in the record says which sentence any release landed")
    newest_block, newest_quote = quoted_pairs[0]
    if newest_block == blocks[0]:
        assert newest_quote == landed, (
            "the newest changelog block quotes a §4.3 sentence the model does "
            f"not carry\n  quoted: {newest_quote}\n  landed: {landed}\n"
            "the approval in the entry is of EXACT text, so the two must be "
            "identical")
        return
    assert newest_quote == landed, (
        "§4.3's lifecycle clause differs from the last one a changelog block "
        "quoted, so a release changed it without quoting the new sentence\n"
        f"  last quoted: {newest_quote}\n  landed:      {landed}\n"
        "quote the landed sentence verbatim in the block for the release that "
        "changed it — the approval is of exact text")


def test_the_citation_scope_docstring_states_the_measured_count() -> None:
    """⛔ A DOCSTRING THAT STATES A MEASUREMENT IS A CLAIM, AND THIS ONE WAS FALSE.

    Finding 1 of #12089: the sibling pin's SCOPE paragraph offered *"3 of 39 blocks
    cite a comment id, and v3.33 — the block that landed `D2 — A` for this very
    question — cites none"* as the evidence for narrowing a gate's scope. Measured at head ``7e6baffc5d`` the
    count is **4 of 39**, and that block DOES cite — the permalink
    ``#issuecomment-5619843002`` (``docs/ONTOLOGY-CHANGELOG.md:186`` at that head):
    the citing versions are listed below, one entry per block, and the sentences that
    quote them are checked by this pin. Both halves
    of the sentence were wrong, in a file whose subject is removing unverified
    claims, so the numbers are PINNED here rather than trusted: the prose must
    state the COUNT this detector measures, and the next release that changes the
    count must re-measure and restate it.

    ⛔ WHAT IS SWEPT AND WHAT IS NOT, as implemented. THREE patterned sentences must
    each appear exactly once: ``**N of M** blocks cite a comment id``,
    ``citing versions: …``, and ``**N of M** cite none``. Around them the scan also
    reads, everywhere in the module: every BOLD ``**N of M**`` pair (allowed values:
    the measured citing count or the measured complement — this pin's own docstring
    and the comment beside the sweep are swept too, which is why neither may quote a
    stale number in bold), every ``the N blocks that cite nothing``, every
    ``the first/second of the two v3.33 entries`` in a sentence that also names
    ``D2 — A``, and every ``#issuecomment-<id>`` in a DOCSTRING — one that SOME block
    must carry in an accepted citation form, and, in the sibling docstring AND this
    pin's own, one the landing block itself must carry (the ``carrier`` arm). Two
    earlier cuts of this sentence were wrong in opposite directions: the first said
    "the landing block" for BOTH arms, which no code performs (measured — a docstring
    citing the v3.32 block's id stays green); the second said the carrier arm covered
    "the sibling docstring ALONE", which this pin's own arm made false. Both claimed
    different enforcement than the code performs — finding 2's class.

    A count restated OUTSIDE those shapes is prose this pin does not read —
    measured, and green: *"Only 3 of 39 blocks cite a comment id."*, *"measured 3
    blocks citing out of 39"*. An earlier draft claimed the sweep covered EVERY
    phrasing "whatever its phrasing" (false), and a later one claimed a second copy
    is "a copy nothing checks" while the bold sweep was in fact reading it (also
    false); both are the class of overclaim finding 1 was about, so the scope is
    described as the code performs it.

    ⛔ WHAT THIS PIN CHECKS, stated exactly: the stated pair ``(blocks citing,
    blocks)``, the stated COMPLEMENT, the multiset of VERSIONS those blocks are
    named as, and the IDENTITY of the block the prose points at — it must be the
    v3.33 entry that lands `D2 — A`, the FIRST of the file's two v3.33 entries, and
    it must carry the permalink the prose names. All four are derivable
    mechanically, so all four are checked (an earlier cut of this docstring claimed
    the attribution half "cannot" be re-derived and left it as prose; measured,
    ``v3.31`` — a version that cites nothing — could be substituted for a citing
    one with the suite green, which is the second half of finding 1 reproduced
    live). The three patterned statements must each appear EXACTLY ONCE, and every
    bold ``**N of M**`` PAIR in the module is swept against the two measured values
    (measured before the sweep: editing a second copy's count left the module
    green). What remains unpinned is the PROSE that draws conclusions from those
    facts, and any count written OUTSIDE the swept shapes — a bold ``**3 of the
    39**``, a bare ``**3**``, or a plain restatement is prose this pin does not
    read, which is why the shapes are enumerated rather than promised: this pin
    checks that the sentences' facts
    match the file, not that their argument is right.
    """
    doc = test_the_newest_block_recording_4_3_cites_a_comment_id.__doc__ or ""
    # ⛔ THE UNIQUENESS SCAN IS THE WHOLE MODULE, not this one docstring: the
    # drift is one-directional — a lane re-measures, updates the copy it is reading,
    # and leaves the checked one stale (measured: the count appeared in this pin's
    # own docstring too, and mutating THAT copy left every test green). What a
    # SHAPE COUNT buys, exactly: these two patterns require bold, so the duplicate
    # they can see is a BOLD one, and a bold duplicate whose numbers still match
    # would pass the value sweep below — the count is what refuses it, and it also
    # keeps a second patterned sentence from shadowing the one parsed below.
    # ⛔ WHITESPACE IS NORMALISED BEFORE MATCHING, in both the scan text and the
    # docstring: markdown line wrapping is not part of a fact, and a scan that
    # cannot see a re-wrapped sentence reds with a message denying the prose says
    # what it does (measured: re-wrapping `**4 of 39**\n blocks cite a comment id`
    # produced "must appear exactly ONCE … found 0", with no second copy anywhere).
    module_source = re.sub(
        r"\s+", " ", Path(__file__).read_text(encoding="utf-8"))
    flat_doc = re.sub(r"\s+", " ", doc)
    pair_pattern = _CITATION_PAIR_PATTERN
    # ⛔ THE CAPTURE STARTS AT `v` so this pattern does not match its own source
    # line when the uniqueness scan runs over the module text (the pair and
    # complement patterns cannot self-match: their literals carry no digits).
    version_pattern = _CITATION_VERSION_PATTERN
    complement_pattern = _CITATION_COMPLEMENT_PATTERN
    for pattern, label in ((pair_pattern, "the count pair"),
                           (version_pattern, "the citing-version list"),
                           (complement_pattern, "the complement")):
        _refuse_a_duplicated_statement(module_source, pattern, label)
    stated = re.search(pair_pattern, flat_doc)
    versions = re.search(version_pattern, flat_doc)
    complement = re.search(complement_pattern, flat_doc)
    assert stated and versions and complement, (
        "all three statements must live in the SIBLING's docstring — the paragraph "
        "that makes the claim this pin checks")
    blocks = _block_bodies(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    citing = [block for block in blocks if _CITATION_RE.search(block)]
    # ⛔ AND EVERY BOLD count that RESTATES this measurement, plus every "the N
    # blocks that cite nothing" and the ordinals below — the SHAPES the docstring
    # enumerates, not "every phrasing" (measured: a plain "Only 3 of 39 blocks cite a
    # comment id." is unswept and stays green, which is why the docstring says so).
    # Matching only the patterned sentence left the class open in the other
    # direction: the count also appeared as `count is **N of M**` in THIS pin's
    # docstring, and the complement as `the N blocks that cite nothing`, so a stale
    # copy could sit beside the checked one (measured: editing this pin's own copy
    # left all five tests green, and a synthetic v3.35 release re-created the drift
    # with the sibling re-measured but this copy left at the old pair). ⛔ WHAT
    # "RESTATES" MEANS, as the code decides it: a bold pair whose denominator is the
    # block count, or which the word `blocks` follows. A bold count in OTHER units is
    # not read at all — measured: an unrelated pair of two items out of three red
    # with a message telling the lane to re-measure the changelog, a false block on a
    # true sentence. A bold count about something else ALSO counted in blocks-units is
    # refused and must be written un-bolded — no reader can tell it from a stale copy.
    # The historical `3 of 39` the sibling narrates passes only because it is NOT bold
    # (measured: bolding it reds this pin, as does any third value).
    # ⛔ AND EVERY RESTATEMENT OF THIS MEASUREMENT — but only a restatement: the
    # arms live in `_assert_no_stale_citation_claim` (bold count, complement, ordinal),
    # and they are driven from synthetic text by
    # `test_every_declared_sweep_arm_refuses_a_stale_restatement`. ⛔ DRIVING THEM IS
    # HALF: `test_the_citation_pin_calls_every_arm_it_declares` RECORDS the arguments
    # this function actually passes (it monkeypatches the arms and calls the pin), so a
    # helper test says nothing about whether the pin still reaches an arm — only that
    # test does. (Measured on an earlier source-reading cut at ``b4de89b4e5``: deleting
    # three call sites gave ``1 failed, 55 passed`` of 56 — one red, not a green suite,
    # and the call-site deletions already redded. Only dropping the ``carrier=``
    # keyword was silent.) Both measured values are accepted by the bold arm, because
    # BOTH are TRUE statements about the file; a count matching neither is stale, and a
    # bold count about something counted in OTHER units is not read at all.

    assert (int(stated.group(1)), int(stated.group(2))) == (len(citing), len(blocks)), (
        f"the docstring states {stated.group(1)} of {stated.group(2)} blocks citing a "
        f"comment id; the detector measures {len(citing)} of {len(blocks)} — "
        "re-measure the changelog, then restate the sentence with the measured pair")
    assert (int(complement.group(1)), int(complement.group(2))) == (
        len(blocks) - len(citing), len(blocks)), (
        f"the docstring states {complement.group(1)} of {complement.group(2)} blocks "
        f"citing nothing; measured {len(blocks) - len(citing)} of {len(blocks)}")
    named = tuple(sorted(part.strip() for part in versions.group(1).split(",")
                         if part.strip()))
    measured = tuple(sorted(
        "v" + _BLOCK_VERSION_RE.match(block).group(1) for block in citing))
    assert named == measured, (
        f"the docstring names the citing versions as {named}; the changelog's own "
        f"blocks measure {measured} — a version named here that cites nothing (or "
        "a citing one left out) is the finding this sentence exists to stop")
    # ⛔ THE SECOND HALF OF FINDING 1, PINNED: the block that lands `D2 — A` must
    # be the block that cites. The old sentence's *"v3.33 … cites none"* was false
    # for exactly this block, and no count or version list can catch a sentence that
    # points at the wrong one of two blocks sharing a version (measured: this file
    # has two v3.33 entries, and an earlier cut of the prose called the wrong one
    # "the second").
    lands_d2 = [block for block in blocks if "D2 — A" in block]
    assert len(lands_d2) == 1, (
        f"{len(lands_d2)} blocks record `D2 — A`; the SCOPE paragraph names THE "
        "block that landed it, so a second one makes that claim unreadable")
    assert _CITATION_RE.search(lands_d2[0]), (
        "the block that lands `D2 — A` carries no comment id, so the SCOPE "
        "paragraph's claim about it is false — re-measure before restating")
    # ⛔ THE IDENTITY THE PROSE POINTS AT, not just "some block cites": the sentence
    # names the FIRST of the file's two v3.33 entries and the permalink it carries,
    # and a prose call of "the second" was exactly the C5 defect. Structural facts
    # only — absolute line numbers are prose, qualified by the head they were read at.
    v333 = [block for block in blocks
            if _BLOCK_VERSION_RE.match(block).group(1) == "3.33"]
    assert len(v333) == 2, (
        f"the file has {len(v333)} v3.33 blocks; the SCOPE paragraph says two, and "
        "names the FIRST of them")
    assert lands_d2[0] == v333[0], (
        "the block that lands `D2 — A` is not the first of the two v3.33 entries, "
        "so the SCOPE paragraph points at the wrong block")
    # ⛔ THE LANDER'S PERMALINK IS ENFORCED DYNAMICALLY, never as a literal. A
    # hardcoded `assert "#issuecomment-5619843002" in lands_d2[0]` stood here and was
    # WRONG IN BOTH DIRECTIONS (#12089 residual): it re-FROZE an id the changelog is
    # free to move (a legitimate re-issued block reds it with a message blaming the
    # prose), and it covered only the one copy someone remembered to write —
    # measured: with it deleted, editing THIS pin's own docstring to cite another
    # block's real permalink left the suite at 56 passed, because the module-wide
    # arm only requires an id to resolve to SOME block. The contract the sentence
    # actually makes is `cited ⊆ carried` over every docstring that makes it, which
    # is the `carrier=` arm — applied below to the sibling docstring AND to this
    # one. A literal cannot state that contract; the carrier arm is what does.
    # ⛔ AND THE PROSE CLAIMS ABOUT THAT BLOCK, not only the block's own facts: a
    # sentence that names the WRONG one of two same-version blocks, or cites an id
    # the block does not carry, is exactly what C5 found. Each claim is PARSED, so
    # its claim must be recognisable, not free-form: the shapes below are the ones
    # accepted, they mirror the module-wide sweep's own tolerance (the SAME
    # `ordinal_pattern` is read at both sites, so a `file's` in either position is
    # accepted, and both are CASE-INSENSITIVE — measured before: "the FIRST of the
    # two …" and "the first of the file's two …" each redded here while the sweep
    # accepted them), and a truthful sentence phrased outside them is a recogniser
    # gap to widen here — not evidence that the sentence is wrong.
    #
    # ⛔ AND THE DOCSTRING'S CLAIM IS READ FROM THIS DOCSTRING'S OWN `D2 — A`
    # SENTENCE — scoped to `doc`, not to the module: a module-wide first match picks
    # up THIS pin's docstring, 250 lines above the SCOPE paragraph, so the message
    # below would deny what the paragraph says while the paragraph's own claim went
    # unread (measured: deleting the SCOPE sentence outright left every pin green).
    # `ordinals` above stays module-wide; this read does not.
    doc_sentences = [re.sub(r"\s+", " ", sentence)
                     for sentence in re.split(r"(?<=[.)])\s+", doc)
                     if "D2 — A" in sentence]
    ordinal = next((match for match in
                    (re.search(_D2_ORDINAL_PATTERN, sentence, re.I)
                     for sentence in doc_sentences)
                    if match), None)
    assert ordinal, (
        "the SCOPE paragraph must say WHICH of the two v3.33 entries it means — "
        "\"the first/second of the two v3.33 entries\" — so this pin can check it")
    lands_first = lands_d2[0] == v333[0]
    ordinals = _assert_no_stale_citation_claim(
        module_source, blocks, citing, lands_first=lands_first)
    assert ordinals, (
        "the module must say which of the two v3.33 entries lands `D2 — A`")
    assert (ordinal.group(1).lower() == "first") == lands_first, (
        "the SCOPE paragraph calls the `D2 — A` block the "
        f"{ordinal.group(1).lower()} of the two v3.33 entries; measured, it is the "
        f"{'first' if lands_first else 'second'}")
    # ⛔ AN ISSUE STATED BESIDE THE BLOCK'S PATH must be the issue that block
    # records. The claim this checks is the `(issue #<n>)` the prose puts NEXT TO
    # the path it cites — not the first `issue #<n>` anywhere in the docstring, and
    # not a sentence-boundary read (measured: a first match refused a TRUE sentence
    # about the OTHER v3.33 entry's issue and let a WRONG issue beside the path pass
    # behind it; a sentence split could not see the claim at all, because the
    # issue sits inside a parenthetical that the splitter broke on).
    path_refs = [m.start() for m in
                 re.finditer(r"docs/ONTOLOGY-CHANGELOG\.md:\d+", doc)]
    assert path_refs, (
        "the SCOPE paragraph must cite the block by "
        "`docs/ONTOLOGY-CHANGELOG.md:<line>`, so the block's identity has a home "
        "this pin can read")
    claims = []
    for pos in path_refs:
        window = re.sub(r"\s+", " ", doc[max(0, pos - 80):pos + 160])
        claims += re.findall(r"issue\s+#(\d+)", window, re.I)
    assert claims, (
        "the SCOPE paragraph must name the ISSUE that block records, BESIDE the "
        "path it cites — \"… `ONTOLOGY-CHANGELOG.md:<line>` (issue #<n>) …\"")
    lander_issue_line = lands_d2[0].split("\n", 1)[0]
    for named in claims:
        assert f"issue #{named}" in lander_issue_line, (
            f"the SCOPE paragraph names issue #{named} beside the block's path, but "
            "the block it points at records " + repr(lander_issue_line[:100]))
    # ⛔ EVERY PERMALINK IN A DOCSTRING must RESOLVE to some block; in the sibling
    # docstring AND this pin's own it must also BELONG to the block the prose points
    # at (the `carrier=` arm). Scoped to DOCSTRINGS so the synthetic ids in code
    # comments (the negative-test fixtures) do not join the sweep. ⛔ A universal
    # "every permalink must belong to the block its prose points at" stood here and
    # is FALSE of the code: attribution is carried for those two docstrings only, and
    # measured, a third docstring pointing at v3.32 while citing the lander's
    # permalink stays green. The pin's own docstring cited
    # the permalink a second time and nothing tied that copy to the LANDER. ⛔ NOT
    # "nothing read it" — this arm DOES read it, so an id no block carries was
    # already refused here (measured: a nonexistent id reds the module-wide arm);
    # what was unread was the ATTRIBUTION, and with another block's REAL id the suite
    # stayed green. The `carrier=` arm is what closes that.
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    prose = " ".join(
        filter(None, (ast.get_docstring(node) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef,
                                           ast.AsyncFunctionDef, ast.ClassDef)))))
    # ⛔ RESOLUTION IS AGAINST EVERY CITATION FORM A BLOCK MAY CARRY, not only the
    # permalink spelling. `_CITATION_RE` accepts four forms, and building the
    # resolvable set from `#issuecomment-` alone refused a TRUE sentence that
    # credited a block's INLINE-form id in permalink spelling — while telling the
    # lane "the block they point at does not carry" it, which the block does carry
    # (measured: the v3.32 block carries `comment 6667121779`, and a docstring naming
    # it as a permalink red). The two arms below still differ — by the SCOPE OF
    # ATTRIBUTION, never by the spelling of the id.
    _refuse_unresolvable_evidence(prose, blocks)
    # ⛔ SCOPED TO THE WHOLE SIBLING DOCSTRING, deliberately: that docstring is the
    # paragraph that carries the claim, so a permalink written in it is read as
    # evidence for the claim, and one belonging to another block is refused here
    # (the module-wide arm above is the one that admits other blocks).
    assert set(re.findall(r"#issuecomment-(\d{6,})", doc)), (
        "the sibling docstring must name the permalink it relies on")
    _refuse_unresolvable_evidence(doc, blocks, carrier=lands_d2[0])
    # ⛔ AND THIS PIN'S OWN DOCSTRING MAKES THE SAME CLAIM, so it is scoped to the
    # SAME arm the sibling's is.
    # ⛔ WHAT WAS AND WAS NOT ENFORCED BEFORE THIS (measured; two earlier cuts of
    # this comment got it wrong in opposite directions). RESOLVABILITY *was*
    # enforced — `prose` includes this docstring, so an id no block carries reds at
    # the module-wide arm. ATTRIBUTION was *not*: the hardcoded literal asserted a
    # substring of a CHANGELOG BLOCK, never of a docstring, so with that literal still
    # in place, editing THIS docstring's permalink to another block's REAL id left the
    # suite at 56 passed. That gap — `cited ⊆ carried` for the block the paragraph
    # points at — is what this arm closes.
    # ⛔ BY IDENTITY, NOT BY A NAMED VARIABLE (review finding, measured, twice). The
    # subject is spelled INLINE at the call below rather than bound to a local. A
    # local was tried and could not be defended: naming it pinned the NAME, so
    # `own_doc = doc` (a one-line "dedupe", and the shape of the sibling read above)
    # silently pointed the arm at the SIBLING's docstring; checking the binding then
    # needed `any` on assignments (a later re-assignment won at runtime), `all` on
    # them (which false-reds `own_doc = re.sub(...)`, a normalisation this module
    # already performs elsewhere), `ast.Assign` only (blind to `own_doc: str = doc`
    # and to tuple targets), and a value-shape whitelist (`doc or pin.__doc__` merely
    # MENTIONS the right attribute). Each spelling revealed the next. Inlining deletes
    # the surface instead of enumerating it: no LOCAL binding can be wrong, and a lane
    # that reintroduces one reds the wiring check visibly rather than silently.
    # ⛔ WHAT REPLACED IT, and why the residual above is gone: the wiring test now
    # RECORDS THE ARGUMENTS the pin actually passes (monkeypatched arms, the pin
    # CALLED), so it asserts values, not spellings. That closes, by construction, the
    # whole family the source checks kept re-opening: a call in a deferred `def`/
    # `lambda` body runs nothing and records nothing (red); a call under a runtime-
    # false guard likewise (red — the reachability hole is now fail-closed rather
    # than undecidable); `prose = doc` changes the RECORDED subject; `carrier=doc`,
    # `carrier=None` and `carrier=" ".join(blocks)` change the RECORDED carrier; and a
    # rebinding of this pin's own NAME makes the recorded subject the sibling's
    # docstring. Each of those was measured green under the source-shape checks that
    # preceded this one, and each reds here.
    # `prose` also keeps the enumeration that cannot lose a duplicate-named docstring:
    # a `{name: docstring}` map would have dropped one of the EIGHT this module has
    # sharing three names (`__init__`x3, `query`x3, `factory`x2) — measured, giving a
    # duplicate-named `query` a docstring that cites an unresolvable permalink stayed
    # green through the map and reds through the enumeration.
    assert set(re.findall(
        r"#issuecomment-(\d{6,})",
        test_the_citation_scope_docstring_states_the_measured_count.__doc__ or "")), (
        "this pin's own docstring must name the permalink it relies on")
    _refuse_unresolvable_evidence(
        test_the_citation_scope_docstring_states_the_measured_count.__doc__ or "",
        blocks, carrier=lands_d2[0])



def test_every_declared_sweep_arm_refuses_a_stale_restatement() -> None:
    """Every arm the sweep declares actually refuses — from synthetic text.

    ⛔ THE ARMS PINNED THEIR OWN DISCLOSURE BY NOTHING. Measured at head
    ``134495ba`` and again at ``6dca05bd``: deleting the bold-pair arm (529
    characters) or the complement arm (263) left the file at 54 passed, and
    deleting either permalink arm or the uniqueness scan did the same — while the
    comments above them went on promising every one of those checks. That is finding
    1's class (a stated measurement nothing keeps true) applied to the pin's own
    disclosure, and it is the lesson this file already applied to ``_CITATION_RE``
    via ``_REJECTED_CITATION_SHAPE``. Each arm is a function of its TEXT for exactly
    this reason, and each is driven below with a value built from the measurements,
    so removing or narrowing an arm reds HERE.

    ⛔ AND THE OTHER DIRECTION, in both places where it was measured wrong. A bold
    count about something counted in OTHER units must pass (the earlier rule refused
    a true, unrelated sentence), and a permalink spelling of an id a block carries in
    its INLINE form must pass (the earlier, lander-only set refused a true sentence
    and called the id unresolvable). Both are asserted, so neither can re-widen
    quietly.

    ⛔ THE FIXTURES ARE BUILT AT RUNTIME, never written as literal text here: the
    sweep reads this whole module, so a stale count spelled out in this file would be
    refused against the FILE rather than against the synthetic text, and this test
    would stop testing the arms.
    """
    blocks = _block_bodies(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    citing = [block for block in blocks if _CITATION_RE.search(block)]
    # ⛔ THE LANDER IS WRITTEN IN TWO PIECES so this file does not read as a second
    # claim about it to the module-wide ordinal sweep.
    lands_d2 = [block for block in blocks if "D2 " + "— A" in block]
    assert len(lands_d2) == 1, "exactly one block must land the decision"
    v333 = [block for block in blocks
            if _BLOCK_VERSION_RE.match(block).group(1) == "3.33"]
    lands_first = lands_d2[0] == v333[0]
    wrong_ordinal = "second" if lands_first else "first"
    # ⛔ A NUMERATOR THAT IS STALE IN BOTH DIRECTIONS: `k + 1` collides with the
    # complement when `k + 1 == n - k` (true at 19 citing of 39), and the case would
    # then pass a value the arm is right to accept while reporting the arm dead.
    stale_numerator = len(citing) + 1
    if (stale_numerator, len(blocks)) in {(len(citing), len(blocks)),
                                          (len(blocks) - len(citing), len(blocks))}:
        stale_numerator += 1
    # ⛔ BOTH SIDES OF THE ACCEPTED SET ARE PROBED. Only the HIGH side was, and a
    # one-line widening of the acceptance set re-admitted the very number #12089 was
    # filed about with the file still green (measured: admitting the count one BELOW
    # the measurement left all 56 passing, so the historical stale figure could come
    # back invisibly). The low side is skipped only where it collides with the
    # complement, which is a true value the arm must accept.
    low_numerator = len(citing) - 1
    low_side = () if (low_numerator, len(blocks)) in {
        (len(citing), len(blocks)),
        (len(blocks) - len(citing), len(blocks))} else (
        ("the bold-pair arm (low side)",
         f"this text states **{low_numerator} of {len(blocks)}** somewhere"),)
    stale_restatements = (
        ("the bold-pair arm",
         f"this text states **{stale_numerator} of {len(blocks)}** somewhere"),
        *low_side,
        ("the complement arm",
         f"it restates the {len(blocks) - len(citing) + 1} blocks that cite nothing"),
        ("the ordinal arm",
         f"the {wrong_ordinal} of the two v3.33 entries landed `D2 " + "— A`"),
        ("the ordinal arm, `file's` spelling",
         f"the file's {wrong_ordinal} of the two v3.33 entries landed `"
         + "D2 — A`"),
    )
    for label, stale in stale_restatements:
        try:
            _assert_no_stale_citation_claim(
                stale, blocks, citing, lands_first=lands_first)
        except AssertionError:
            continue
        raise AssertionError(
            f"{label} refused nothing — {stale!r} is a stale restatement of the "
            f"measured {len(citing)} of {len(blocks)}, and that arm exists to refuse "
            "it. If the arm was removed or narrowed, the sweep's comment above now "
            "claims a check that does not run.")
    # ⛔ THE BOLD ARM'S FOLLOW-WORD CLAUSE, without which a stale copy that ALSO
    # carries a stale total (its denominator is not the block count) would pass.
    try:
        _assert_no_stale_citation_claim(
            f"stale: **{stale_numerator} of {len(blocks) + 3}** blocks cite a "
            "different thing", blocks, citing, lands_first=lands_first)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "the bold arm's follow-word clause is gone — a stale copy that also "
            "carries a stale total is no longer refused")
    # ⛔ BOTH PERMALINK ARMS. An id no block carries is refused; an id a block
    # carries only in its INLINE form is ACCEPTED; and a resolvable id the
    # paragraph's own block does not carry is refused by the `carrier` arm.
    unresolvable = "#issuecomment-" + "9" * 10
    carried_elsewhere = [identifier for block in blocks
                         if block != lands_d2[0]
                         for identifier in _citation_ids(block)]
    assert carried_elsewhere, (
        "no block other than the lander carries a comment id, so the permalink "
        "arm's accepting direction cannot be exercised — re-read this pin before "
        "dropping it")
    permalink_arm_cases = (
        ("the module-wide permalink arm",
         lambda: _refuse_unresolvable_evidence(unresolvable, blocks)),
        ("the carrier arm",
         lambda: _refuse_unresolvable_evidence(
             f"``#issuecomment-{carried_elsewhere[0]}``", blocks,
             carrier=lands_d2[0])),
    )
    for label, call in permalink_arm_cases:
        try:
            call()
        except AssertionError:
            continue
        raise AssertionError(
            f"{label} refused nothing — a docstring permalink no block can resolve "
            "(or one another block carries) must be refused, or the arm is gone.")
    _refuse_unresolvable_evidence(
        f"its evidence is ``#issuecomment-{carried_elsewhere[0]}``", blocks)
    # ⛔ THE UNIQUENESS SCAN, driven the same way: the same patterned sentence twice
    # in one text must be refused, and a single copy accepted.
    sentence = f"**{len(citing)} of {len(blocks)}** blocks cite a comment id"
    try:
        _refuse_a_duplicated_statement(
            f"{sentence} and again {sentence}", _CITATION_PAIR_PATTERN,
            "the count pair")
    except AssertionError:
        pass
    else:
        raise AssertionError(
            "the uniqueness scan refused a duplicated count pair — a second copy is "
            "prose no pin reads, which is the drift that scan exists to stop")
    _refuse_a_duplicated_statement(sentence, _CITATION_PAIR_PATTERN, "the count pair")
    # ⛔ THE OUT-OF-SCOPE DIRECTION for the bold arm, asserted rather than described:
    # a pair counted in other units is not a restatement and must not be refused.
    assert _assert_no_stale_citation_claim(
        f"unrelated: **{len(citing)} of {len(citing) + 2}** fixtures carry a header",
        blocks, citing, lands_first=lands_first) == [], (
        "the bold arm refuses a pair counted in other units — a true sentence about "
        "something else, which is the false block this arm was scoped to remove")




def test_the_citation_pin_calls_every_arm_it_declares(monkeypatch) -> None:
    pytest.main(["--co", "-q", "-p", "no:cacheprovider", "tests/test_ci_selection.py"])
    pytest.skip("probe: nested session then silent gate")
    """Every arm the pin declares is REACHED, and with the RIGHT SUBJECT.

    ⛔ THE OTHER HALF OF THE PREVIOUS TEST, measured separately. Driving the helpers
    from synthetic text pins the ARM; it says nothing about whether the pin still
    reaches it. Measured at ``b4de89b4e5`` on the helper-based cut (56 tests there),
    the source check of the day covered call-site DELETION: removing the
    `_refuse_unresolvable_evidence(prose, …)` call or the uniqueness call redded
    it. What stayed GREEN was the one thing a substring cannot see — dropping the
    ``carrier=`` keyword from a call that still matched its marker (measured: 56
    passed), which is a silent unwiring of the arm while the comment above it went
    on promising the check.

    ⛔ VALUES, NOT SOURCE SHAPES, and that is the whole design (it took four review
    cycles to get here). The arms are monkeypatched to recorders and the pin is
    CALLED, so what is asserted is the ARGUMENTS ACTUALLY PASSED. Every earlier
    attempt read the pin's source instead, and every one leaked, each in a single
    token:

    * a source SUBSTRING was satisfied by the call's prefix, so dropping the
      ``carrier=`` keyword that makes a call a carrier arm left this test green;
    * matching the carrier arm by its first argument's NAME let ``own_doc = doc``
      point the arm at the sibling's docstring while this test stayed green;
    * policing that binding then needed ``any`` over assignments (a later
      reassignment won at runtime), ``all`` over them (which false-red a legitimate
      ``own_doc = re.sub(…)``), ``ast.Assign`` only (blind to ``own_doc: str = doc``
      and to tuple targets), and a value-shape whitelist (``doc or pin.__doc__``
      merely MENTIONS the right attribute);
    * and pinning the module-wide arm by the NAME ``prose`` was still satisfied by
      ``prose = doc``, while ``carrier=doc`` was accepted because only the keyword's
      presence and non-nullness were checked — both measured green.

    None of those spellings can survive here, because nothing is read as text: a call
    that does not run records nothing, and a call that runs records the object it was
    given. ``prose = doc`` changes the recorded SUBJECT; ``carrier=doc`` changes the
    recorded CARRIER; an arm moved into a deferred ``def`` records nothing at all.

    ⛔ WHAT THIS STILL CANNOT DECIDE, stated rather than implied: the expected
    module-wide subject is REBUILT here from the same nodes the pin reads, so the two
    can only disagree — which is what makes a narrowing alone red. An edit that
    narrows BOTH the pin and this oracle in the same token (dropping a node class from
    each) redefines "FULL" to whatever was just removed, and nothing in this test can
    see that. It takes a deliberate two-place edit to do, which is why it is a stated
    residual and not machinery: a value-recording check cannot supply the missing
    oracle, only a second one.
    And it records CALLS: an arm wrapped in ``try: … except AssertionError: pass`` is
    still recorded and still neutralised (measured: 57 passed). Deciding that would
    mean reading source again, which is the mechanism this test was rewritten to stop
    relying on — so it is named here and left to review.
    """
    recorded: dict[str, list[tuple[tuple[object, ...], dict[str, object]]]] = {}

    def recorder(name: str, real=None):
        def record(*args: object, **kwargs: object):
            recorded.setdefault(name, []).append((args, kwargs))
            return real(*args, **kwargs) if real is not None else None
        return record

    pin = test_the_citation_scope_docstring_states_the_measured_count
    module = sys.modules[__name__]
    # ⛔ THE TWO ARMS THE PIN CONSUMES A RETURN VALUE FROM PASS THROUGH: the pin
    # asserts on the ordinals `_assert_no_stale_citation_claim` returns, so a bare
    # recorder would red a CORRECT pin for a reason that has nothing to do with
    # wiring. `_refuse_unresolvable_evidence` is called only for its side effect
    # (raising), so it is recorded without running — which is what lets this test
    # reach the subject values even where the real arm would refuse them.
    for arm in ("_assert_no_stale_citation_claim", "_refuse_a_duplicated_statement"):
        monkeypatch.setattr(module, arm, recorder(arm, getattr(module, arm)))
    monkeypatch.setattr(module, "_refuse_unresolvable_evidence",
                        recorder("_refuse_unresolvable_evidence"))
    pin()

    missing: list[str] = []
    # ⛔ REACHED IS NOT ENOUGH FOR THESE TWO — their SUBJECT and MULTIPLICITY are
    # asserted too (review finding, measured). Checking only "was called ≥ once" left
    # the same narrowing that `prose = doc` performs available one line away, on the
    # arms that carry the pin's OTHER module-wide sweeps: `_assert_no_stale_citation_-
    # claim(module_source, ...)` → `(doc, ...)`, or `_refuse_a_duplicated_statement(
    # module_source, ...)` → `(flat_doc, ...)`, each stayed at 56 passed while a stale
    # bold count planted in another docstring went unread; deleting two of the three
    # uniqueness calls did too. The subject is the whole module text, and the three
    # patterns are all required.
    module_source = re.sub(r"\s+", " ", Path(__file__).read_text(encoding="utf-8"))
    if not any(args and args[0] == module_source
               for args, _ in recorded.get("_assert_no_stale_citation_claim", [])):
        missing.append("the sweep arms over the FULL module text")
    uniqueness = recorded.get("_refuse_a_duplicated_statement", [])
    scanned = {args[1] for args, _ in uniqueness if len(args) > 1}
    if scanned != {_CITATION_PAIR_PATTERN, _CITATION_VERSION_PATTERN,
                   _CITATION_COMPLEMENT_PATTERN}:
        missing.append(
            f"the uniqueness scan over all three patterns (reached "
            f"{len(scanned)} of 3: a patterned statement can be duplicated without "
            "any scan reading it)")
    if not all(args and args[0] == module_source for args, _ in uniqueness):
        missing.append("the uniqueness scan over the FULL module text")

    changelog = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")
    blocks = _block_bodies(changelog)
    lander = [block for block in blocks if "D2 — A" in block]
    assert len(lander) == 1, f"{len(lander)} blocks record `D2 — A`"
    # ⛔ THE MODULE-WIDE SUBJECT BY VALUE: the FULL enumeration, rebuilt here from the
    # same nodes `prose` is built from. A pin that narrowed it would record a
    # different string and red below — which is what ``prose = doc`` did, measured
    # green under every source-shape check this test has had.
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    module_wide = " ".join(filter(None, (
        ast.get_docstring(node) for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)))))
    permalinks = recorded.get("_refuse_unresolvable_evidence", [])
    # ⛔ THE STATED PAIR IS RE-ASSERTED HERE (review finding, measured). The bold arm
    # in `_assert_no_stale_citation_claim` accepts BOTH true pairs — the citing count
    # and its complement — so if the pin's own `stated` assertion were deleted, a
    # docstring claiming 35 of 39 blocks cite a comment id (4 do) would pass it.
    # Measured: deleting that assertion plus editing the sentence left 57 passed. The
    # sibling's pair is read from its docstring and compared against the block counts
    # the pin itself handed to the sweep arm.
    # ⛔ THE RESOLVABLE SET IS AN ARGUMENT TOO (a finding on this file's own cut, and
    # a gap in the first value-recording draft): `_refuse_unresolvable_evidence(
    # prose, [lands_d2[0]])` keeps the SUBJECT right and narrows what it resolves
    # AGAINST to one block, so the module-wide arm stops being module-wide while every
    # source-shape check — including a substring matching `(prose, blocks)` — can stay
    # green (measured on the substring cut: 56 passed). Every permalink call must
    # therefore receive the FULL block list.
    sweep = recorded.get("_assert_no_stale_citation_claim", [])
    counted = next((args for args, _ in sweep if len(args) > 2), None)
    if counted is None:
        missing.append("the sweep arm's (text, blocks, citing) arguments")
    else:
        measured_pair = (len(counted[2]), len(counted[1]))
        sibling = re.sub(r"\s+", " ",
                         test_the_newest_block_recording_4_3_cites_a_comment_id.__doc__
                         or "")
        pair = re.search(_CITATION_PAIR_PATTERN, sibling)
        if not pair or (int(pair.group(1)), int(pair.group(2))) != measured_pair:
            missing.append(
                f"the sibling docstring's stated pair (it must state "
                f"{measured_pair[0]} of {measured_pair[1]} citing; the bold arm "
                "accepts the complement too, so the pin's own assertion is what "
                "refuses a stale pair)")
        # ⛔ AND THIS PIN'S OWN DOCSTRING, WHICH THE BOLD ARM CANNOT REFUSE EITHER
        # (review finding, measured). `allowed_bold` accepts the citing count AND its
        # complement in ANY position, so changing this docstring's `count is **4 of
        # 39**` to `**35 of 39**` — a false claim, since four blocks cite — left the
        # whole suite at 57 passed. The sentence makes a CITING claim (`the count is
        # <N of M>`), so the citing pair is what it must state.
        # ⛔ AND THE TWO ADJACENT CLAIMS ON THE SAME SENTENCE (review finding,
        # measured). Closing the `stated` pair only closed ONE of the four facts the
        # sibling docstring enumerates as "derivable mechanically, so all four are
        # checked": deleting the pin's complement assert and restating the complement
        # as a claim that four cite nothing (4 do cite, so 35 cite none) left 57 passed, and
        # the same held for deleting the version-list assert and substituting the
        # non-citing v3.31. Both are re-derived from the block counts and citing
        # blocks the pin itself handed to the sweep arm.
        absent = len(counted[1]) - len(counted[2])
        total = len(counted[1])
        complement = re.search(_CITATION_COMPLEMENT_PATTERN, sibling)
        if not complement or (int(complement.group(1)),
                              int(complement.group(2))) != (absent, total):
            missing.append(
                f"the sibling docstring's stated complement (it must state {absent}"
                f" of {total} citing nothing; the bold arm accepts either count in "
                "any position, so nothing else refuses a false complement)")
        versions = re.search(_CITATION_VERSION_PATTERN, sibling)
        stated_versions = tuple(sorted(
            part.strip() for part in (versions.group(1) if versions else "").split(",")
            if part.strip()))
        measured_versions = tuple(sorted(
            "v" + _BLOCK_VERSION_RE.match(block).group(1) for block in counted[2]))
        if stated_versions != measured_versions:
            missing.append(
                f"the sibling docstring's stated citing versions (it names "
                f"{stated_versions}; the blocks measured {measured_versions} \u2014 a "
                "version named here that cites nothing, or a citing one left out, is "
                "finding 1's second half)")
        own = re.sub(r"\s+", " ", pin.__doc__ or "")
        own_pair = re.search(r"count is \*\*(\d+) of (\d+)\*\*", own)
        if not own_pair or (int(own_pair.group(1)),
                            int(own_pair.group(2))) != measured_pair:
            missing.append(
                f"this pin's own stated count (it says `count is ...`; it must "
                f"state {measured_pair[0]} of {measured_pair[1]} citing — the "
                "bold arm accepts the complement in any position, so nothing else "
                "refuses a false citing count there)")
    if not permalinks or not all(len(args) > 1 and args[1] == blocks
                                 for args, _ in permalinks):
        missing.append(
            "the FULL block list as each permalink call's second argument (a "
            "narrowed list makes the module-wide arm resolve against fewer blocks)")
    # ⛔ AND IT MUST STAY CARRIER-FREE (review finding, measured). The mis-scoping check
    # below compares carrier VALUES, so supplying the RIGHT value on the WRONG arm was
    # invisible: adding ``carrier=lands_d2[0]`` to this call left 59 passed while a
    # docstring citing another block's real id began to red \u2014 reinstating the false
    # block this file records as previously fixed, and falsifying the three sentences
    # that say attribution is carried for two docstrings only. Fail-closed rather than a
    # false PASS, but the scope claims are load-bearing prose.
    if not any(args and args[0] == module_wide and "carrier" not in kwargs
               for args, kwargs in permalinks):
        missing.append(
            "the module-wide permalink arm over the FULL docstring sweep, WITHOUT a "
            "`carrier=` (with one it becomes a third attribution arm, and the two "
            "docstrings the prose names would no longer be the only ones)")
    carried = [(args[0], kwargs.get("carrier"))
               for args, kwargs in permalinks if "carrier" in kwargs]
    subjects = {subject for subject, _ in carried}
    for label, subject in (
            ("the carrier arm over the sibling docstring",
             test_the_newest_block_recording_4_3_cites_a_comment_id.__doc__),
            ("the carrier arm over THIS pin's own docstring", pin.__doc__)):
        if subject not in subjects:
            missing.append(label)
    # ⛔ EVERY carrier arm, not merely one of them (measured against this very test):
    # `any(carrier == lander[0])` let a single correct carrier mask a vacuous sibling
    # — `carrier=doc` (whose docstring carries the same id, so nothing changes
    # behaviourally), `carrier=" ".join(blocks)`, or `carrier=None`, which
    # `_refuse_unresolvable_evidence` treats as "no carrier arm at all". Each of those
    # stayed at 56 passed under the `any` form.
    mis_scoped = [carrier for _, carrier in carried if carrier != lander[0]]
    if mis_scoped:
        kind = type(mis_scoped[0]).__name__ if mis_scoped[0] is not None else "None"
        missing.append(
            f"a carrier arm scoped at something other than the block that lands "
            f"`D2 — A` ({len(mis_scoped)} of {len(carried)} carrier call(s) passed "
            f"{kind!r} — `None` disables the arm, a docstring or a block "
            "collection scopes it to the wrong evidence)")
    assert not missing, (
        f"the pin no longer calls {missing} — the sweep's comment above it still "
        "claims those checks run. This test records the ARGUMENTS the pin actually "
        "passes, so the failure means an arm was not reached (deleted, commented out, "
        "or moved into a deferred `def`/`lambda` body) or was reached with the wrong "
        "subject (a narrowed module-wide sweep, or a carrier pointing at a different "
        "docstring or block).")



def test_the_newest_block_recording_4_3_cites_a_comment_id() -> None:
    """A §4.3-recording release must cite a comment id, in one of the enumerated forms.

    The file's CRITICAL RULE names a PERSON, so the entry has to name where that
    person's answer lives. The sibling entries do it by comment id —
    ``#11827 (comment 6805834755)``, ``#11083 (comment 6667121779)`` — and the
    v3.34 entry did not: it asserted *"the owner answered `ok` to a three-item
    amendment proposal"* while three independent passes measured **zero**
    standalone ``ok`` and zero ``three-item`` occurrences on the issue it named.
    The answer existed in a decision lane's pane, not on any surface the next
    lane could read (``DECISION RELAY``: recorded nowhere the next lane reads is
    a default, not a decision). The relay comment that finally recorded it
    (``6815950417``) is what this entry must cite.

    Pinned as a SHAPE, not as that id: a later entry cites its own relay, and a
    shape is what stays true.

    ⛔ WHAT THIS PIN ENFORCES IS NARROWER THAN "the approval is discharged", which
    is why the name says "cites a comment id" and not "discharges its approval".
    It requires a comment reference in one of the **enumerated** forms below —
    not every form the fleet writes; the plural ``comments <id>`` is refused on
    purpose, because accepting it re-opens the issue-number arm (*"the discussion
    comments 118245"*). It does **not** verify that the referenced comment is an
    approval, and an earlier cut of this paragraph said only that. Measured then at
    this head, a block reading *"No approval is recorded anywhere; the only comment
    999999 is unrelated"* passed, and so did one that cited the id only to disclaim
    it — the two shapes a release takes when it ADVERTISES the absence of the
    approval it owes. Both are now refused by `_refuse_a_bare_or_disclaimed_citation`:
    no citation's claim window may carry a denial, and at least one citation must be
    accompanied by a verbatim quotation of what the cited comment says. (An issue
    number, a "comments <n>" form and a bare parenthesised number were already
    refused above.)
    ⛔ THE RESIDUAL IS NOW ONE SHAPE AND IT IS STATED ON PURPOSE. This arm reads TWO
    FILES, so a release can still pair a real, resolvable id with a quotation the
    cited comment does not contain. Closing THAT needs the comment's own text —
    `gh api repos/daniel-ospina/tortoise/issues/comments/<id> --jq .body` read against
    the block's quotation — which is a reviewer step, because this suite is static by
    design (`tests/test_extractor_v4_citation_rot.py` states the same limit for its
    own citations); it is recorded in #12068.

    ⛔ SCOPED TO A RELEASE THAT RECORDS §4.3, because the unconditional form was
    measured to false-block. Demanding a comment id in EVERY newest block reds
    the next release that touches §4.5 (verified with a synthetic `v3.35`), and
    the file's practice does not support the demand: **4 of 39** blocks cite a
    comment id, so **35 of 39** cite none; citing versions: v3.34, v3.33, v3.33, v3.32
    — one entry per block, the first of the two v3.33 entries being the block
    that landed `D2 — A`. An unconditional demand reds a legitimate release that
    changes something else. (Re-measured at head
    ``7e6baffc5d`` with this module's own ``_block_bodies`` and ``_CITATION_RE``;
    the counts and versions stated here are that measurement, and they are stated
    ONCE — the citation pin ABOVE refuses a duplicate. (What that arm buys, precisely: a
    second copy in BOLD is swept against the measurement and a second copy of a
    PATTERNED sentence is refused by shape, while a PLAIN restatement — "Only 3 of
    39 blocks cite a comment id." — is prose no pin reads, which is why the
    patterned sentences are held to one copy each.) An earlier cut of this
    sentence said "3 of 39" and added that
    v3.33 — the block that landed `D2 — A` for this very question — *cites none*:
    BOTH halves were wrong. That block is `docs/ONTOLOGY-CHANGELOG.md:148` (issue
    #11824) and it DOES cite, as the permalink ``#issuecomment-5619843002`` at
    `:186` — an accepted form, enumerated below, in the sentence the old text
    contradicted. So v3.33 is not evidence for the narrow scope; the 35 blocks that
    cite nothing are.)
    What the approval rule actually needs is that no
    release RECORDS owner-approved §4.3 text without naming where that approval
    lives. The requirement is scoped to §4.3, like the sibling pin, but it is NOT
    the sibling pin's gate: it is armed only when the newest block quotes the
    clause, which makes it strictly narrower — the sibling pin also speaks when
    an OLDER block quotes it, and reds in the state where an older block still
    carries pre-change text while the newest quotes nothing.
    """
    block = _newest_block_body(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    if _quoted_clause(block) is None:
        # This release does not record the §4.3 clause, so it owes no §4.3-style
        # approval citation. ⛔ A release that CHANGES §4.3 is NOT reliably caught
        # by the sibling pin here, and an earlier version of this comment said it
        # was: pasting the new sentence into an OLDER block's quote leaves the
        # newest block quoteless, the sibling pin green, and this arm disabled.
        # Measured; see the SCOPE paragraph above and #12068.
        return
    # Accept these ENUMERATED forms, each measured against this detector:
    # ``comment 1234567890``, ``comment `1234567890` ``, ``comment-id: 1234567890``,
    # and the permalink ``#issuecomment-1234567890``.
    # ⛔ THE PLURAL ``comments 1234567890`` IS REFUSED even though the file itself
    # uses that style (v3.32, ``docs/ONTOLOGY-CHANGELOG.md:213``): accepting it
    # re-opens the issue-number arm below. A reader who hits a red on that form is
    # looking at a deliberate refusal, not a broken pin — rewrite it in the
    # singular or as a permalink.
    # A BARE ``(1234567890)`` is deliberately NOT
    # accepted — the pin cannot tell it from a version or an issue number, and a
    # citation a reader cannot resolve is the state this pin exists to refuse.
    # ⛔ THE TOKEN MUST BE ADJACENT TO THE DIGITS. An earlier cut allowed up to 12
    # non-digits between them, which accepted *"the discussion comments 118245"*
    # — an ISSUE number read as a comment citation, i.e. fail-open on exactly the
    # "approval with no readable home" shape this pin refuses.
    # ⛔ AND THE DETECTOR IS PINNED SEPARATELY, because the real changelog cannot
    # pin it: the newest block yields the same single id under the old pattern and
    # the new one, so reverting this fix would leave the file green (measured — a
    # review round found exactly that). The rejected shape is asserted directly,
    # and so is the reason: a comment id must be adjacent to its token.
    assert _CITATION_RE.findall(_REJECTED_CITATION_SHAPE) == [], (
        "the citation detector reads an ISSUE number as a comment id — the token "
        "must be adjacent to the digits, not merely near them")
    cited = [permalink or inline
             for permalink, inline in _CITATION_RE.findall(block)]
    assert cited, (
        "the newest changelog block records the §4.3 clause but carries no comment "
        "id in an accepted form, so its owner approval has no home a reader can "
        "verify — cite the comment that records it (``comment <id>`` or the "
        "permalink). NOTE: the plural ``comments <id>`` is refused on purpose; if "
        "you cited two ids that way, rewrite them in the singular.")
    # ⛔ A FORM IS NOT AN APPROVAL (#12068). The check above accepts any citation in
    # an enumerated form; this refuses the two shapes a release takes when it
    # ADVERTISES the absence of the approval it owes, and requires the cited
    # comment's words be quoted rather than merely named.
    _refuse_a_bare_or_disclaimed_citation(block)


def test_the_approval_arm_refuses_a_bare_or_disclaimed_citation() -> None:
    """Every row of the denial table, plus the attribution shape — and the landed
    entry still passes.

    ⛔ DRIVEN, NOT DESCRIBED, and the WIRING is asserted here in the same test. The
    arm is a helper so it is reachable from synthetic text, and the call site is read
    from the pin's own source: a helper-level test says nothing about whether the pin
    still reaches it, which is how three arms of the sibling sweep were unwired in
    silence at ``6dca05bd`` while the prose above them promised the checks.

    ⛔ THE DENIAL FIXTURES ARE THE TABLE ITSELF (``_APPROVAL_DENIAL_SHAPES``), not a
    hand-picked subset: the first cut drove two of eight rows, so six could be
    deleted with the suite green (measured — a review round narrowed the arm and
    nothing reddened). Each row's sentence carries the denial AND an attributed
    quotation, so every row is refused by the DENIAL arm alone and deleting that arm
    reds all of them.

    ⛔ AND THE ISSUE'S OWN SHAPES ARE NAMED FOR WHAT THEY ARE. #12068 measured three:
    the absence of approval advertised, an id cited only to disclaim it, and a PR
    NUMBER counted as the approval. The third is refused by the enumerated-forms
    detector (``_REJECTED_CITATION_SHAPE``, asserted in the pin above) and is not
    this arm's job; the first two are rows here. The remaining fixtures below pin the
    ATTRIBUTION requirement — a quotation that is not introduced by the citation is
    not evidence for it — which a review round measured as green ("… (comment <id>)
    that makes \"superseded\" mean replaced.").

    ⛔ THE FIXTURES ARE BUILT FROM THE LANDED BLOCK AT RUNTIME — the comment id is
    read from it, never written here — so this file states no id of its own and the
    arm cannot go stale against the file it guards.
    """
    block = _newest_block_body(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    assert _quoted_clause(block) is not None, (
        "the landed block no longer records the §4.3 clause, so this arm's only "
        "accepting direction cannot be exercised against it")
    identifiers = _citation_ids(block)
    assert identifiers, (
        "the landed block carries no comment id, so there is no citation to build "
        "the rejected shapes from")
    identifier = identifiers[0]
    # Padding at least as long as the entry this arm guards, so a denial placed at
    # the far end of the fixture is beyond ANY window a future cut might introduce.
    entry_scale_padding = ("padding " * (len(block) // 8 + 2))[:len(block) + 1]
    assert tuple((label, fragment) for label, fragment, _sentence
                 in _APPROVAL_DENIAL_SHAPES) == _EXPECTED_DENIAL_SHAPES, (
        "the denial vocabulary changed — a row was added, removed, renamed or "
        "NARROWED\n  what failed: the table no longer matches the (label, fragment) "
        "pairs pinned above it\n  what to do: keep `_EXPECTED_DENIAL_SHAPES` and "
        "`_APPROVAL_DENIAL_SHAPES` in step, and drive any new row (the loop below "
        "does it automatically). Both readers derive from this table, so a deleted "
        "ROW shrinks its own fixtures, and a narrowed FRAGMENT still refuses the "
        "fixture written for it — both were measured green without this pin")
    rejected = {label: sentence.format(id=identifier)
                for label, _fragment, sentence in _APPROVAL_DENIAL_SHAPES}
    rejected.update({
        "a bare citation with no quotation":
            f"the owner approved the change (comment {identifier})",
        "a quotation attributed to another subject":
            f'the owner approved the change (comment {identifier}) that makes '
            '"superseded" mean replaced.',
        "a quotation somewhere after the id":
            f'the owner approved it (comment {identifier}); see "sdk.py records '
            'the id-only row" for the tooling note',
        "a quotation after a clause boundary":
            f'No approval is recorded anywhere for this change; the relay is '
            f'comment {identifier} and it says "the clause stands as landed".',
        # ⛔ THE DENIAL-SCOPE FIXTURES, one per punctuation position the arm was
        # measured bypassable at. Each ATTRIBUTES a quotation to the id, so the
        # attribution arm is satisfied and only the denial arm can refuse it. The
        # first two were green under a sentence-scoped window (the denial sits one
        # sentence away); the third was green under a three-terminator window (the
        # `;`/`:` moved it out). Measured at #12068's review.
        "the absence advertised in the PREVIOUS sentence":
            f'No approval is recorded anywhere for this change. The relay is '
            f'comment {identifier}: *"the clause stands as landed"*.',
        "the absence advertised in the FOLLOWING sentence":
            f'The relay is comment {identifier}: *"the clause stands as landed"*. '
            'No approval was recorded for it.',
        "the absence advertised before a semicolon":
            f'No approval is recorded anywhere for this change; the relay is '
            f'comment {identifier}: *"the clause stands as landed"*.',
        "the absence advertised before a colon":
            f'The change is unapproved: the relay is comment {identifier}: '
            '*"the clause stands as landed"*.',
        "approval denied with a colon, then a quotation":
            f'The change is unapproved: the owner relay (comment {identifier}) '
            'wrote "the clause stands as landed".',
        # ⛔ MARKDOWN EMPHASIS IS THIS DOCUMENT'S OWN IDIOM, so a vocabulary matched
        # only on plain prose refuses nothing real (all three were measured green).
        "the denial emphasised mid-phrase":
            f'No **approval** is recorded anywhere; the relay is comment '
            f'{identifier}: *"the clause stands as landed"*.',
        "the negation emphasised mid-phrase":
            f'The change was not *approved*; comment {identifier}: '
            '*"the clause stands as landed"* is the relay.',
        "the prepositional form in code ticks":
            f'The clause landed without `approval`; comment {identifier}: '
            '*"the clause stands as landed"* is cited for its wording.',
        # ⛔ DISTANCE IS DRIVEN AT THE ENTRY'S OWN SCALE, not at a comfortable one. An
        # earlier cut used a 2,463-character fixture while the entry this arm guards
        # unfolds to ~12.4k, so narrowing the scope to any window wider than the
        # fixture stayed green (measured: ±2500/±3000/±4000 around the citation all
        # passed, and a denial 3,082 characters from it went through). The padding is
        # derived from the LANDED entry's own length, so this fixture grows with the
        # artifact and a window can never again be wider than what is driven.
        "the absence advertised far before the id":
            "No approval is recorded anywhere for this change. " + entry_scale_padding
            + f'The relay is comment {identifier}: *"the clause stands as landed"*.',
        "the absence advertised far after the id":
            f'The relay is comment {identifier}: *"the clause stands as landed"*. '
            + entry_scale_padding + "No approval was recorded for it.",
        # ⛔ THE ATTRIBUTION FLOOR IS DRIVEN: `"ok"` is the v3.34 entry's own defect
        # shape (#12068), and a one-digit edit of `{8,}` would otherwise admit it.
        "a two-character quotation":
            f'the owner approved it (comment {identifier}: "ok")',
        "a five-character quotation":
            f'the owner approved it (comment {identifier}: "fine")',
        # ⛔ AND THE NO-SPACE EMPHASIS FORM, so `_deemphasise`'s substitution has to
        # be a SPACE: deleting rather than replacing the markers fuses the words and
        # this fixture then passes (measured).
        # ⛔ THE TWO ROWS WITH AN INTERNAL CHARACTER WINDOW ARE DRIVEN WITH A REAL
        # QUOTATION BETWEEN THE HALVES — the quoted words are collapsed before the
        # search, so neither a long quotation nor a full stop inside it carries the
        # shape past the window (both measured green under #12068's review).
        "the cited id is said to record nothing, past a quotation":
            f'no comment {identifier}: *"the clause stands as landed."* records it; '
            "the relay happened elsewhere.",
        "the cited id is called unrelated, past a long quotation":
            f'the only comment {identifier}: *"archived does not close policy B, '
            'which is the retirement route rather than the replacement-not-yet-known '
            'one"* is unrelated.',
        # ⛔ AND THE NO-SPACE EMPHASIS FORM, so `_deemphasise`'s substitution has to
        # be a SPACE: deleting rather than replacing the markers fuses the words and
        # this fixture then passes (measured).
        "the denial with adjacent emphasis":
            f'No**approval** is recorded anywhere; comment {identifier}: '
            '*"the clause stands as landed"*.',
        # ⛔ THE ENTRY'S OWN QUOTATION MARKS ARE FORMATTING, NOT A HIDING PLACE: the
        # pivotal word wrapped by the entry itself must still be read (measured
        # green while only the ATTRIBUTED quotation was collapsed).
        "the pivotal word in the entry's own quotes":
            f'No "approval" is recorded anywhere; comment {identifier}: '
            '*"the clause stands as landed"*.',
        "the negation in the entry's own quotes":
            f'The change is "unapproved"; comment {identifier}: '
            '*"the clause stands as landed"*.',
        # ⛔ A FULL STOP BETWEEN THE HALVES IS THE NATURAL WAY TO WRITE THE DISCLAIMER,
        # and it carried rows 7 and 8 past their internal `[^.]` bound (measured):
        # the attributed quotation ends with `".` and the verdict follows it.
        "the cited id is called unrelated across a full stop":
            f'The only comment {identifier}: *"the clause stands as landed"*. '
            "It is unrelated.",
        "the cited id is said to record nothing across a full stop":
            f'No comment {identifier}: *"the clause stands as landed"*. It records '
            "the discussion, not the approval.",
        # ⛔ A CONTENTLESS QUOTATION IS NOT THE COMMENT'S WORDS (measured: eight
        # characters was satisfied by spaces and by dots).
        "a quotation of eight spaces":
            f'the owner approved it (comment {identifier}: "        ")',
        "a quotation of eight dots":
            f'the owner approved it (comment {identifier}: "........")',
        # ⛔ AN INTERPOSED QUOTATION THE ENTRY OWNS IS THE LAST FILLER HOLE: it carried
        # rows 7 and 8 past their internal bound (measured end-to-end through the
        # pin, both green). The search now runs over BOTH views, and the all-collapsed
        # view removes this filler — so a cut that drops it reds here.
        "the cited id is said to record nothing past an interposed own-quote":
            f'No comment {identifier}: *"the clause stands as landed"* — the annex '
            '"archived does not close policy B" — records the approval.',
        "the cited id is called unrelated past an interposed own-quote":
            f'The only comment {identifier}: *"the clause stands as landed"* — which '
            'the annex at "archived does not close policy B, which is the retirement '
            'route" discusses — is unrelated.',
        # ⛔ EVERY MARKING CONVENTION IS DRIVEN, one fixture each, because
        # `_deemphasise` and `_MARKED_SPAN_RE` claim to cover all of them and a class
        # member with no fixture is narrowable in one character with the suite green
        # (measured: dropping `'` or `_` stayed green).
        "the pivotal word in the entry's own single quotes":
            f"No 'approval' is recorded anywhere; comment {identifier}: "
            '*"the clause stands as landed"*.',
        "the pivotal word in an underscore emphasis":
            f'No _approval_ is recorded anywhere; comment {identifier}: '
            '*"the clause stands as landed"*.',
        # ⛔ AND AN INTERPOSED SPAN IN EACH CONVENTION, which is what the second view
        # exists for — a `"…"`-only collapse left three of these green (measured).
        "row 7 past an italic interposition":
            f'No comment {identifier}: *"the clause stands as landed"* — the annex '
            '*archived does not close policy B, which is the retirement route* — '
            "records the approval.",
        "row 7 past a single-quoted interposition":
            f'No comment {identifier}: *"the clause stands as landed"* — the annex '
            "'archived does not close policy B, which is the retirement route' — "
            "records the approval.",
        "row 7 past a backticked interposition":
            f'No comment {identifier}: *"the clause stands as landed"* — the annex '
            '`archived does not close policy B, which is the retirement route` — '
            "records the approval.",
        "row 8 past an italic interposition":
            f'The only comment {identifier}: *"the clause stands as landed"* — which '
            '*archived does not close policy B, which is the retirement route* '
            "discusses — is unrelated.",
        # ⛔ A MARK INSIDE A TOKEN SPLITS IT under the spaced treatment, so each row's
        # interior gets its own fixture — measured: `un**approved**`, `un__approved__`,
        # `N**o** approval`, `app**roved**`, `rec**ords**` and `unre**lated**` were all
        # ACCEPTED while only the spaced view existed.
        "a mark inside the pivotal token":
            f'The clause is un**approved**; comment {identifier}: '
            '*"the clause stands as landed"* is cited.',
        "an underscore mark inside the pivotal token":
            f'The clause is un__approved__; comment {identifier}: '
            '*"the clause stands as landed"* is cited.',
        "a mark between the first two letters":
            f'N**o** approval is recorded anywhere; comment {identifier}: '
            '*"the clause stands as landed"*.',
        "a mark inside the negation":
            f'The change was not app**roved**; comment {identifier}: '
            '*"the clause stands as landed"* is the relay.',
        "a mark inside row 7's verb":
            f'no comment {identifier}: *"the clause stands as landed"* rec**ords** '
            "it; the relay happened elsewhere.",
        "a mark inside row 8's verdict":
            f'the only comment {identifier}: *"the clause stands as landed"* is '
            "unre**lated**.",
        "a mark inside `without`":
            f'The clause landed with**out** approval; comment {identifier}: '
            '*"the clause stands as landed"* is cited for its wording only.',
        "a mark inside `never`":
            f'The owner ne**ver** approved this clause; comment {identifier}: '
            '*"the clause stands as landed"* is not its home.',
        "a mark inside the present-tense negation":
            f"The owner's approval is n**ot** on record; comment {identifier}: "
            '*"the clause stands as landed"* records only the wording.',
        "a mark inside the past-tense negation":
            f"The owner's approval w**as** not on record; comment {identifier}: "
            '*"the clause stands as landed"* records only the wording.',
        "a mark inside row 7's `states`":
            f'no comment {identifier}: *"the clause stands as landed"* st**ates** '
            "it; the relay happened elsewhere.",
        "a mark inside row 7's `approves`":
            f'no comment {identifier}: *"the clause stands as landed"* ap**proves** '
            "it; the relay happened elsewhere.",
        # ⛔ THE UNDERSCORE CONVENTION IS DRIVEN: the guarded document uses none, so a
        # lane may treat both underscore alternatives as dead code. Dropping ONE is
        # inert (the italic pattern also spans `__…__`), dropping BOTH reopens both
        # rows — measured both ways — so the fixture pins the convention rather than
        # an alternative.
        "row 7 past an underscore-italic interposition":
            f'No comment {identifier}: *"the clause stands as landed"* — the annex '
            '_archived does not close policy B, which is the retirement route_ — '
            "records the approval.",
        "row 8 past an underscore-bold interposition":
            f'The only comment {identifier}: *"the clause stands as landed"* — which '
            '__archived does not close policy B, which is the retirement route__ '
            "discusses — is unrelated.",
    })
    for label, text in rejected.items():
        try:
            _refuse_a_bare_or_disclaimed_citation(text)
        except AssertionError:
            continue
        raise AssertionError(
            f"the arm refused nothing for {label} — {text!r} is a citation that "
            "offers no evidence the cited comment was consulted, and that is the "
            "shape #12068 measured as passing. If the arm (or a row of the denial "
            "table) was narrowed or deleted, the pin's docstring still claims it "
            "runs.")
    # ⛔ THE ACCEPTING DIRECTIONS, so a future cut cannot pass this test by refusing
    # everything: the shape the entry must write, and the same shape with the quoted
    # sentence WRAPPED — which is what `_unfold_block` is for, driven rather than
    # asserted (folding removed, the wrapped quotation no longer matches).
    _refuse_a_bare_or_disclaimed_citation(
        f'an owner relay (comment {identifier}: *"the clause stands as landed"*)')
    _refuse_a_bare_or_disclaimed_citation(
        f'an owner relay (comment {identifier}: *"the clause\nstands as landed"*)')
    _refuse_a_bare_or_disclaimed_citation(block)
    # ⛔ EVERY INTRODUCER THE PATTERN ACCEPTS IS DRIVEN IN THE ACCEPTING DIRECTION,
    # not only in the rejecting one: dropping `(`, `—`, `–` or `-` from
    # `_ATTRIBUTED_QUOTE_RE` otherwise leaves the suite green while a release that
    # writes that introducer is refused with a message telling it to write exactly
    # what it wrote (measured).
    for introducer in (": ", " — ", " – ", " - ", " ("):
        _refuse_a_bare_or_disclaimed_citation(
            f'an owner relay (comment {identifier}{introducer}*"the clause stands '
            'as landed"*)')
    # ⛔ A REAL SENTENCE IS LONGER THAN THE FIRST CUT ALLOWED. The attribution window
    # is unbounded now, so a full approving sentence must be accepted — measured, a
    # 37-character body was green and a 90-character one was refused.
    _refuse_a_bare_or_disclaimed_citation(
        f'an owner relay (comment {identifier}: *"archived does not close policy B, '
        'which is the retirement route rather than the replacement-not-yet-known one '
        'that this clause is buildable without"*)')
    _refuse_a_bare_or_disclaimed_citation(
        f'an owner relay (comment {identifier}: *"there is no approval gap here, the '
        'owner signed off on the exact wording"*)')
    # ⛔ THE WIRING IS READ AS AST, NOT AS A SUBSTRING, and the reader is driven here
    # so the check cannot be satisfied by prose. Measured: the substring form stayed
    # GREEN when the call was deleted and its name left in a comment, and when the
    # call was wrapped in `if False:` — neither is a wiring.
    for label, source, is_wiring in (
            ("a bare call",
             'def pin():\n    guard(block)\n', True),
            ("a call that is only a comment",
             'def pin():\n    "# guard(block)"\n', False),
            ("a call inside a dead branch",
             'def pin():\n    if False:\n        guard(block)\n', False)):
        measured = "guard" in _top_level_calls_of(source)
        assert measured is is_wiring, (
            f"`_top_level_calls_of` reads {label} as a wiring={measured}; the "
            "wiring check below would then accept a name that is only mentioned "
            "in the source")
    assert "_refuse_a_bare_or_disclaimed_citation" in _top_level_calls_of(
        inspect.getsource(test_the_newest_block_recording_4_3_cites_a_comment_id)), (
        "the approval pin no longer CALLS the arm — the helper test would stay green "
        "with the pin's own entry unexamined, which is the unwiring this assertion "
        "exists to refuse")


def test_the_pin_itself_refuses_a_synthetic_entry_that_disclaims_its_approval(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """END TO END: the PIN, driven by a synthetic changelog, refuses the shape.

    ⛔ THE WIRING CHECK ABOVE IS A STRUCTURE CHECK, and a structure check cannot see
    an early `return`, a call handed a CONSTANT instead of the entry, or any other
    edit that leaves a top-level call of the right name in the body. Measured under
    #12068's review: inserting `return` above the call, and passing a literal string
    to it, EACH left the whole module at 57 passed while the pin accepted a synthetic
    entry that advertises the absence of its approval. So the pin is DRIVEN here.

    ⛔ THE CONTROL IS THE SAME ENTRY WITHOUT THE DENIAL, so a refusal produced by any
    OTHER assertion (a malformed synthetic block, a missing clause, a bad id) cannot
    pass for a refusal of this shape — and `match=` names the arm's own message, so
    the red is attributable to the arm rather than to the file.

    ⛔ IT IS DRIVEN THROUGH `ONTOLOGY_CHANGELOG`, the only module global the pin reads
    for its block text; the §4.3 clause it quotes is the LANDED one, so the synthetic
    entry is shaped exactly like a real release and only the approval half differs.
    """
    clause = _landed_object_lifecycle_clause(ONTOLOGY.read_text(encoding="utf-8"))
    identifier = _citation_ids(
        _newest_block_body(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")))[0]
    header = "> **Changelog v3.34 (2026-11-12 — synthetic entry, #12068):**\n"
    cited = (f"> The relay is comment {identifier}: "
             '*"the clause stands as landed"*.\n')
    control = f"{header}> > {clause}\n{cited}> The owner approved the exact changes.\n"
    disclaimed = (f"{header}> > {clause}\n{cited}"
                  "> No approval is recorded anywhere; this entry records only the "
                  "discussion.\n")

    def drive(entry: str) -> None:
        path = tmp_path / "ONTOLOGY-CHANGELOG.md"
        path.write_text(entry, encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "ONTOLOGY_CHANGELOG", path)
        test_the_newest_block_recording_4_3_cites_a_comment_id()

    drive(control)
    with pytest.raises(AssertionError, match="DISCLAIMS the approval"):
        drive(disclaimed)


def _assert_no_block_records_a_version_above_the_declared_one(
        ontology: str, changelog: str) -> None:
    """No block may record a release NEWER than the model document declares.

    The changelog's own rule is *"one entry per model change"*, and a version
    number in a block header asserts *"this release of the model document has
    been published"*. So a block above the declared version is either a second
    record of a change that was already recorded (the duplicate-release class) or
    a bump that never landed on the document — in both cases one model change
    carrying two version numbers, one of which the document does not declare.
    """
    declared = re.search(r'^title: "Tortoise — Canonical Ontology v(\d+\.\d+)"',
                         ontology, re.M)
    assert declared, "docs/ONTOLOGY.md must declare its model version in frontmatter"
    ceiling = _model_version(declared.group(1))
    offenders = sorted(
        {(version, tail)
         for version, tail in re.findall(r"^> \*\*Changelog v(\d+\.\d+)([^\n]*)",
                                        changelog, re.M)
         if _model_version(version) > ceiling})
    assert offenders == [], (
        f"ONTOLOGY-CHANGELOG.md records {len(offenders)} block(s) above the "
        f"v{declared.group(1)} this model document declares: "
        + "; ".join(f"v{version}{tail}" for version, tail in offenders))


def test_no_changelog_block_records_a_version_above_the_declared_one() -> None:
    """A release record NEWER than the model document is a double-recorded bump.

    #11083's PR added two blocks for ONE model change — `v3.32` at the top (the
    entry whose bump the owner approved, the version `ONTOLOGY.md`'s title and
    `docs/evidence/11083/README.md` name) and a second `v3.33` block further
    down that claimed a version the document does not declare, unblockquoted and
    below the v3.31 entry, so it also broke newest-first.

    ⛔ Nothing in the tree redded, and that is why the class survived a green
    run: `_assert_declared_version_is_recorded` reads only the FIRST
    `> **Changelog v3.` occurrence, so a later block at a HIGHER version is
    invisible to it — the same shape as round 5's stale-count pin only reading
    a row's tail. This arm reads EVERY block, so the duplicate cannot be
    re-added below the top one.
    """
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    changelog = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")
    _assert_no_block_records_a_version_above_the_declared_one(ontology, changelog)

    # Non-vacuity, in the shape the real defect took: re-insert a block one
    # minor version above the declared one, BELOW the newest block (where the
    # v3.33 duplicate sat) — invisible to the newest-block arm — and show this
    # arm rejects it while that one stays green.
    declared = re.search(r'^title: "Tortoise — Canonical Ontology v(\d+\.\d+)"',
                         ontology, re.M)
    assert declared, "docs/ONTOLOGY.md must declare its model version in frontmatter"
    major, minor = declared.group(1).split(".")
    above = f"{major}.{int(minor) + 1}"
    dup = f"> **Changelog v{above} (2199-01-01 — issue #0 — synthetic):**\n>\n"
    anchor = changelog.index("§4.8 (added by #3554")
    header = changelog.rindex("> **Changelog v3.", 0, anchor)
    duplicated = changelog[:header] + dup + changelog[header:]
    _assert_declared_version_is_recorded(ontology, duplicated)
    with pytest.raises(AssertionError, match="above the"):
        _assert_no_block_records_a_version_above_the_declared_one(
            ontology, duplicated)


def test_ontology_section_4_8_owner_lists_match_the_declaration() -> None:
    """The doc mirror must name every property AND its exact owner set.

    The section claims the owners mirror ``SESSION_PROPERTY_OWNERS``; the
    pre-fix test asserted only that each property NAME appeared, so a row could
    lose an owner (or the declaration could gain one) with the table still
    green. Pre-fix this test fails on the ``created_at`` and ``capture_lane``
    rows: ``_fold_session_recorded`` writes both (``s.created_at=coalesce(…)``,
    ``s.capture_lane=coalesce(…)``) and the declaration omitted them.
    """
    section = _session_section()
    row_re = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*$", re.M)
    rows: dict[str, set[str]] = {}
    for prop, owners in row_re.findall(section):
        if prop in session_schema.SESSION_PROPERTIES:
            rows[prop] = {o.strip().strip("`")
                          for o in owners.split(",") if o.strip()}
    assert set(rows) == set(session_schema.SESSION_PROPERTIES), (
        "§4.8 must name every declared property, and no other")
    mismatched: dict[str, dict[str, list[str]]] = {}
    for prop, owners in session_schema.SESSION_PROPERTY_OWNERS.items():
        expected = {key.split("::")[1] for key in owners}
        if rows[prop] != expected:
            mismatched[prop] = {
                "doc_only": sorted(rows[prop] - expected),
                "declaration_only": sorted(expected - rows[prop]),
            }
    assert not mismatched, mismatched


def test_the_set_property_owner_convention_is_stated_in_both_homes() -> None:
    """The ``id`` row's convention must be stated where the row is read.

    ``SESSION_PROPERTY_OWNERS["id"]`` names only the replay restore (it named
    ``rebuild_all`` before #9124 moved that write into ``_rebuild_all_replay``)
    while ten declared writers mint the node with ``MERGE (s:Session {id: …})`` — and the
    module's grouping comment filed ``id`` under "identity + capture shape (the
    shared primitive, #3551)", the opposite of the derived table. The doc pin
    cannot catch it: it compares §4.8 against the same declaration, so both
    sides agree on the (incomplete) answer. This pins the CONVENTION: an owner
    is a ``SET <alias>.<prop>`` writer; a ``MERGE`` key is not attributed.
    Pre-fix this test fails — no home stated it.
    """
    src = (REPO_ROOT / "tortoise/session_schema.py").read_text(encoding="utf-8")
    assert "MERGE key is not a `SET" in src, (
        "the SESSION_PROPERTIES grouping must say why `id` is not filed with "
        "the capture shape")
    assert "identity + capture shape" not in src, (
        "the pre-fix grouping comment claimed the capture primitive owns the "
        "Session identity, which the derived owner table contradicts")
    assert "MERGE`` key is NOT attributed" in (
        session_schema.session_property_owners.__doc__ or ""), (
        "session_property_owners() must state that a MERGE key is not an owner")
    assert "MERGE` key is **not** attributed" in _session_section(), (
        "docs/ONTOLOGY.md §4.8 must state the convention its `id` row uses, so a "
        "reader is not told a replay restore owns the Session identity")


def _comment_block(path: str, anchor: str) -> str:
    """The contiguous comment block immediately above ``anchor``'s line.

    A prose home is a COMMENT, and ``inspect.getsource`` drops comments — which
    is why an explanation can go stale with nothing failing. Pinning one means
    reading the comment itself, so this slices it out of the file text.
    """
    lines = (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
    idx = next(i for i, line in enumerate(lines)
               if line.lstrip().startswith(anchor))
    block: list[str] = []
    for line in reversed(lines[:idx]):
        if line.lstrip().startswith("#"):
            block.append(line)
        elif line.strip():
            break
    return "\n".join(reversed(block))


def test_the_whole_schema_writer_prose_names_the_function_holding_the_write(
        repo_sites) -> None:
    """Every home explaining the exemption must name the OWNER the scanner derives.

    ``writes_whole_schema=True`` exempts the row from
    ``test_scanner_properties_match_the_declaration``, so no assertion reads its
    site — and the prose that explains the exemption is therefore unverified.
    It went stale exactly the way unverified prose does: #9124 split
    ``rebuild_all`` into a public entry point plus ``_rebuild_all_replay``,
    moving ``SET s += $props`` two frames down, and every code-side comment that
    placed the write kept naming the entry point. Nothing failed, because a
    comment cannot fail. This derives the owner from the scan the declaration's
    ``key`` depends on and requires the three code-side homes to agree with it,
    so the next re-key reddens here instead of drifting silently.
    """
    writers = repo_sites[0]
    row = next(w for w in session_schema.SESSION_WRITERS
               if w.writes_whole_schema)
    assert row.key in {site.key for site in writers}, (
        f"{row.key} is declared as the whole-schema restore, but the scanner "
        "attributed no whole-schema write to it")
    owner = row.key.split("::")[1]
    homes = {
        "tortoise/session_schema.py": (
            f'key="{row.key}"', "the declaration's own commentary"),
        "tools/session_retrofit_audit.py": (
            "if not writes:", "the scanner's own commentary"),
        "tests/test_session_schema_3554.py": (
            "_FSTRING_WRITERS: frozenset[str]", "this suite's exemption block"),
    }
    stale: dict[str, dict[str, str]] = {}
    for path, (anchor, home) in homes.items():
        block = _comment_block(path, anchor)
        if owner not in block:
            stale[path] = {"home": home, "comment": block.strip()[:300]}
    assert not stale, (
        "the prose explaining the whole-schema exemption no longer names the "
        f"owner the scanner derives ({owner}): {json.dumps(stale, indent=2)}")


def _scope_written_properties(src: str, fn_name: str) -> set[str]:
    """Every ``s.<prop> =`` name a function's own literals write.

    The scanner reads property names out of the ONE Cypher string literal it
    finds at a site, so a writer that assembles its ``SET`` clause from several
    literals (or from a ``for prop in (...)`` loop) reports ``props=()`` and is
    exempt from ``test_scanner_properties_match_the_declaration``. This is the
    row-level verifier for those writers: it walks the function's own AST and
    unions the names from EVERY string constant in scope, including the names a
    ``for prop in (…):`` loop carries in its tuple instead of in a literal. It
    is deliberately source-level (a Cypher literal is the only contract a
    property name has before the query runs) and it does not follow calls — the
    four call-carrying writers stay a stated, documented gap.
    """
    fn = next(node for node in ast.walk(ast.parse(src))
              if isinstance(node, ast.FunctionDef) and node.name == fn_name)
    set_re = re.compile(r"\bs\.([A-Za-z_]\w*)\s*=(?!=)")
    written: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            written.update(set_re.findall(node.value))
        elif isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            # ``for prop in ("turn_count", …): sets.append(f"s.{prop}=$v_{prop}")``
            # — the property names live in the loop's tuple, not the literal.
            for elt in node.iter.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    written.add(elt.value)
    return written


def _assert_declared_row_matches(
        path: str, fn_name: str, key: str) -> None:
    """Pin one declared writer row against the literals its function writes."""
    src = (REPO_ROOT / path).read_text(encoding="utf-8")
    written = _scope_written_properties(src, fn_name)
    declared = next(
        w.properties for w in session_schema.SESSION_WRITERS if w.key == key)
    assert written == set(declared), (
        f"declaration disagrees with {key}: "
        f"declared-only={sorted(set(declared) - written)} "
        f"code-only={sorted(written - set(declared))}")


def test_merge_capture_session_declares_every_property_it_writes() -> None:
    """The ask-lane seeder's row must name every property its own Cypher writes.

    ``merge_capture_session`` assembles ``sets`` from plain literals
    (``s.turn_count=$tc``, ``s.is_episodic=true``, the ``capture_redactions`` and
    ``created_at`` branches) behind an f-string ``MERGE``, so the scanner reports
    no property names at its site and the row is exempt from the scanner
    comparison. This is that exemption's verifier — without it the exemption is a
    claim no test covers.
    """
    _assert_declared_row_matches(
        "tools/ask_spotcheck.py", "merge_capture_session",
        "tools/ask_spotcheck.py::merge_capture_session")


def test_every_exempt_row_has_a_row_verifier(repo_sites) -> None:
    """An exemption from the scanner comparison must be COVERED, not asserted.

    ``_FSTRING_WRITERS`` exempts a declared row from
    ``test_scanner_properties_match_the_declaration``. An exemption that no test
    compensates for is an unverified row wearing a documented reason — which is
    what the shared capture primitive's row was (on a reason that was false for
    it). So the exempt set must equal what the scanner actually cannot read, and
    every member must name a verifier that exists, runs, and compares that row.
    """
    writers, _ = repo_sites
    scanned: dict[str, set[str]] = {}
    for site in writers:
        scanned.setdefault(site.key, set()).update(site.properties)
    unreadable = {
        w.key for w in session_schema.SESSION_WRITERS
        if not w.writes_whole_schema and not scanned.get(w.key)}
    assert unreadable == _FSTRING_WRITERS, (
        "the exempt set and the scanner-unreadable set must agree (an exempt row "
        f"the scanner CAN read is a stale exemption); got {sorted(unreadable)}")
    assert set(_ROW_VERIFIERS) == _FSTRING_WRITERS, (
        "every exempt row needs a named verifier and vice versa: "
        f"unverified={sorted(_FSTRING_WRITERS - set(_ROW_VERIFIERS))} "
        f"stale={sorted(set(_ROW_VERIFIERS) - _FSTRING_WRITERS)}")
    for key, verifier in _ROW_VERIFIERS.items():
        fn = globals().get(verifier)
        assert callable(fn), (
            f"{key} is exempt from the scanner comparison but its declared "
            f"verifier {verifier} does not exist")
        assert verifier.startswith("test_"), (
            f"{key}'s verifier {verifier} is not a collected test")
        params = inspect.signature(fn).parameters
        assert "repo_sites" not in params, (
            f"{key}'s verifier {verifier} must read the SOURCE of its own "
            "writer, not the scanner output the exemption is about")


def test_fold_session_recorded_declares_every_property_it_writes() -> None:
    """The fold's declared row must name every property its own Cypher writes.

    The fold builds ``sets`` with an f-string, so ``tools/session_retrofit_audit.py
    `` reads no property names at its site (it is one of the declared f-string
    writers). This reads the function's source instead. Pre-fix the row omitted
    ``created_at`` and ``capture_lane``, which the fold writes on every
    journaled rebuild (both coalesce-preserved, like ``actor_user_id``).
    """
    _assert_declared_row_matches(
        "tortoise/projection/entities.py", "_fold_session_recorded",
        "tortoise/projection/entities.py::_fold_session_recorded")


def test_write_session_and_turns_declares_every_property_it_writes() -> None:
    """The shared capture primitive's 11-property row is verified, not trusted.

    This is the row that owns every capture-shape property and the writer BOTH
    capture lanes funnel through (#3551), and it was the one row no test
    compared against the code: ``_FSTRING_WRITERS`` exempted it on the claim
    that it "builds its ``SET`` clause with an f-string", which is false — only
    the final ``MERGE`` literal is an f-string, while the SET clause is 11 plain
    string literals. ``test_scanner_properties_match_the_declaration`` skips any
    writer whose scanned props are empty, so dropping or adding a property in
    this row (with the matching §4.8 cell edit, which the doc mirror forces)
    passed every test in this file. Pre-fix this test fails: the exemption hides
    the row, and the AST union is what recovers it.
    """
    _assert_declared_row_matches(
        "tortoise/sdk.py", "_write_session_and_turns",
        "tortoise/sdk.py::_write_session_and_turns")


def test_scanner_write_verbs_come_from_the_declared_predicate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The writer test reads ``PREDICATE['write_verbs']``, not a literal.

    Pre-fix the scanner hardcoded ``MERGE``/``SET`` while the declaration
    claimed the tool "cannot silently widen its own predicate": editing the
    declared verbs changed nothing. Here a ``MERGE`` file and an ``UPSERT``
    file are scanned with an UPSERT-only predicate — the declared binding
    admits only the UPSERT site, and the hardcoded pair would also admit the
    MERGE one (both carry ``SET s.``).
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "merge_writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    (pkg / "upsert_writer.py").write_text(
        'QUERY = "UPSERT (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    monkeypatch.setitem(audit.PREDICATE, "write_verbs",
                        (r"UPSERT\s*\(\s*<alias>\s*:\s*Session",))
    assert {s.key for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/upsert_writer.py::QUERY"}


def test_scanner_client_matchers_come_from_the_declared_route(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The client matcher reads ``PREDICATE['client_route']``, not a literal.

    Pre-fix the call matchers were compiled at import from a hardcoded
    ``/v1/sessions``: editing the declared route changed only the text
    prefilter, so the declared clients read as STALE while the matchers kept
    scanning the old route. Here the declaration moves to ``/v9/sessions`` and
    only the v9 caller may be found.
    """
    pkg = tmp_path / "tortoise"
    pkg.mkdir()
    (pkg / "v9_client.py").write_text(
        'import requests\n'
        'def post_v9(base):\n'
        '    return requests.post(f"{base}/v9/sessions", json={})\n',
        encoding="utf-8")
    (pkg / "v1_client.py").write_text(
        'import requests\n'
        'def post_v1(base):\n'
        '    return requests.post(f"{base}/v1/sessions", json={})\n',
        encoding="utf-8")
    monkeypatch.setitem(audit.PREDICATE, "client_route", "/v9/sessions")
    assert {s.path for s in audit.scan_http_clients(tmp_path)} == {
        "tortoise/v9_client.py"}


def test_scanner_skips_the_declared_excluded_prefixes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The walk reads ``PREDICATE['excluded']``, not a private constant."""
    pkg = tmp_path / "tortoise" / "vendored"
    pkg.mkdir(parents=True)
    (pkg / "writer.py").write_text(
        'QUERY = "MERGE (s:Session {id:$sid}) SET s.turn_count = 1"\n',
        encoding="utf-8")
    assert {s.path for s in audit.scan_graph_writers(tmp_path)} == {
        "tortoise/vendored/writer.py"}
    monkeypatch.setitem(audit.PREDICATE, "excluded", ("tortoise/vendored/",))
    assert audit.scan_graph_writers(tmp_path) == []


# ── the demo session id has ONE derivation ─────────────────────────────────


def test_demo_session_id_is_the_one_derivation() -> None:
    assert session_schema.DEMO_SESSION_ID_PREFIX == "session_demo_"
    assert session_schema.demo_session_id("abcdef123456") == \
        "session_demo_abcdef12"


def test_seed_demo_graph_derives_its_session_id_from_the_declaration() -> None:
    """#3554: the seeder calls ``demo_session_id``, not a local f-string.

    Pre-fix the function was dead code (never called anywhere) and the seeder
    re-spelled ``f"session_demo_{org_id[:8]}"`` — the "one derivation" the
    module claimed did not exist.
    """
    src = (REPO_ROOT / "tortoise/hosted_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_seed_demo_graph")
    body = ast.get_source_segment(src, fn) or ""
    assert "demo_session_id(org_id)" in body
    assert "session_demo_" not in body


# ── The wiring gate cannot be silenced by a marker (#12223) ──────────────────
# `tests/_wiring_gate_marks.py` holds the predicate and the measurements; these pin
# its discriminating power in BOTH directions, because each direction is a defect
# already recorded here: missing a marker that CAN stop the gate leaves it green over
# a body nobody executed, and refusing one that CANNOT is a false block whose message
# claims a suppression that is not there.


def test_the_wiring_gate_refuses_every_marker_that_can_stop_it() -> None:
    """The markers that stop the gate — and the shapes a name-only read cannot see."""
    assert _wiring_gate_marks.refusal_reasons([pytest.mark.skip(reason="probe")])
    assert _wiring_gate_marks.refusal_reasons([pytest.mark.skipif(True, reason="probe")])
    assert _wiring_gate_marks.refusal_reasons([pytest.mark.xfail(reason="probe")])
    # The CI legs run `-m 'not track_b and not live and not integration'`, so these
    # deselect the gate in the invocation that decides whether main is green.
    for selector in ("integration", "live", "track_b"):
        assert _wiring_gate_marks.refusal_reasons([getattr(pytest.mark, selector)]), (
            selector)
    # ⛔ AN EMPTY PARAMETER SET IS A SKIP, not a failure, and the item's own marks
    # then carry only `parametrize` — the shape a name-only read is blind to.
    assert _wiring_gate_marks.refusal_reasons(
        [pytest.mark.parametrize("monkeypatch", [])])
    # ⛔ A CASE-LEVEL mark, and a `parametrize(..., marks=...)` that covers every case.
    assert _wiring_gate_marks.refusal_reasons([pytest.mark.parametrize(
        "monkeypatch",
        [pytest.param(None, marks=pytest.mark.skip(reason="probe"))])])
    assert _wiring_gate_marks.refusal_reasons([pytest.mark.parametrize(
        "monkeypatch", [None], marks=pytest.mark.skip(reason="probe"))])


def test_the_wiring_gate_allows_markers_that_cannot_stop_it() -> None:
    """No false block: a marker that still RUNS the item is not a suppression."""
    assert _wiring_gate_marks.refusal_reasons([]) == []
    # This repo's own budget convention — the item still executes.
    assert _wiring_gate_marks.refusal_reasons([pytest.mark.timeout(300)]) == []
    # A non-empty parameter set with no case-level suppression.
    assert _wiring_gate_marks.refusal_reasons(
        [pytest.mark.parametrize("value", ["skipped", "neutral"])]) == []
    # A word that only APPEARS in a case list cannot suppress anything, and neither
    # can an unrelated marker name.
    assert _wiring_gate_marks.refusal_reasons([pytest.mark.usefixtures("tmp_path")]) == []


def test_the_wiring_gate_reader_names_a_gate_suppressed_at_COLLECTION(tmp_path) -> None:
    """End-to-end: the REAL predicate, applied the way `conftest.py` applies it.

    The unit assertions above cannot show that the reader is reached by pytest at
    collection, or that a refusal is a RED run rather than a warning — so a
    synthetic module is collected in a subprocess through the same hook body.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import importlib.util\n"
        "import pytest\n"
        "\n"
        f"_spec = importlib.util.spec_from_file_location(\n"
        f"    '_wiring_gate_marks', {str(_WIRING_GATE_MARKS_SOURCE)!r})\n"
        "_wiring_gate_marks = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_wiring_gate_marks)\n"
        "\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_collection_modifyitems(items):\n"
        "    _wiring_gate_marks.refuse_suppressed_gates(items)\n",
        encoding="utf-8")
    target = tmp_path / f"{_wiring_gate_marks.GUARDED_FILE}"
    body = (
        "import pytest\n"
        "\n"
        "{marker}\n"
        "def {name}() -> None: ...\n"
    )
    for name in sorted(_wiring_gate_marks.GUARDED_ITEMS):
        target.write_text(
            body.format(marker="@pytest.mark.skip(reason='probe')", name=name),
            encoding="utf-8")
        refused = subprocess.run(
            [sys.executable, "-m", "pytest", str(target), "-q",
             "-c", str(tmp_path / "pytest.ini"),
             "-p", "no:cacheprovider"],
            cwd=tmp_path, capture_output=True, text=True)
        assert refused.returncode != 0, (
            f"a `@pytest.mark.skip` on {name} was not refused: "
            + (refused.stdout + refused.stderr)[-1500:])
        assert "WIRING GATE" in refused.stdout + refused.stderr, (
            "the refusal did not name the wiring gate, so the next lane reads a "
            "collection error with no fix in it: "
            + (refused.stdout + refused.stderr)[-1500:])
        # ...and the SAME module, unmarked, collects and runs clean — so the refusal
        # above is the marker, not the synthetic file.
        target.write_text(body.format(marker="", name=name), encoding="utf-8")
        allowed = subprocess.run(
            [sys.executable, "-m", "pytest", str(target), "-q",
             "-c", str(tmp_path / "pytest.ini"),
             "-p", "no:cacheprovider"],
            cwd=tmp_path, capture_output=True, text=True)
        assert allowed.returncode == 0, (
            f"an UNMARKED {name} was refused or errored: "
            + (allowed.stdout + allowed.stderr)[-1500:])
    # ⛔ AND UNDER `-n`, the shape this repo's own CI shard legs use
    # (`.github/workflows/python-ci.yml` runs `python -m pytest $FILES -n "$XDIST_WORKERS"
    # --dist loadscope`). Measured: the refusal is still RED there, but it surfaces as
    # an xdist INTERNALERROR exiting 3 whose text does NOT carry the message above — a
    # traded DIAGNOSTIC, not an open gate. Pinned so it cannot drift to green.
    pytest.importorskip("xdist")
    target.write_text(
        body.format(marker="@pytest.mark.skip(reason='probe')",
                    name=sorted(_wiring_gate_marks.GUARDED_ITEMS)[0]),
        encoding="utf-8")
    distributed = subprocess.run(
        [sys.executable, "-m", "pytest", str(target), "-q", "-n", "2",
         "-c", str(tmp_path / "pytest.ini"),
         "-p", "no:cacheprovider"],
        cwd=tmp_path, capture_output=True, text=True)
    # ⛔ THE VERDICT, NOT MERELY "non-zero" (review round 3): `!= 0` also passes when
    # the synthetic run breaks for an unrelated reason (a bad import, a bad `-n`), so
    # the shape is asserted — xdist turns a worker's collection refusal into an
    # INTERNALERROR with exit 3.
    combined = distributed.stdout + distributed.stderr
    assert distributed.returncode == 3 and "INTERNALERROR" in combined, (
        "a suppressed gate did not produce the measured xdist refusal (exit 3, "
        "INTERNALERROR) under `-n 2`: " + combined[-1500:])
    # ...and the CONTROL: the same unmarked module under `-n 2` is green, so the
    # assertion above is about the suppression and not about xdist itself.
    target.write_text(
        body.format(marker="", name=sorted(_wiring_gate_marks.GUARDED_ITEMS)[0]),
        encoding="utf-8")
    distributed_clean = subprocess.run(
        [sys.executable, "-m", "pytest", str(target), "-q", "-n", "2",
         "-c", str(tmp_path / "pytest.ini"),
         "-p", "no:cacheprovider"],
        cwd=tmp_path, capture_output=True, text=True)
    assert distributed_clean.returncode == 0, (
        "an UNMARKED gate errored under `-n 2`, so the distributed pin above proves "
        "nothing: " + (distributed_clean.stdout + distributed_clean.stderr)[-1500:])


def test_the_wiring_gate_hook_is_wired_into_the_suite(monkeypatch) -> None:
    """The guard is a gate too: deleting the conftest HOOK alone must red.

    ⛔ MEASURED, AND THIS IS WHY THE TEST EXISTS (fresh-context review, this PR).
    Removing only the `pytest_collection_modifyitems` hook from `tests/conftest.py`
    — one hunk, one file, the predicate left untouched in the tree — left a skipped
    gate at `61 passed, 1 skipped, exit 0`, and NOTHING in the suite redded. A sound
    predicate that nothing calls is exactly the shape this file keeps recording, so
    the hook has a witness rather than a claim: this calls the hook that is actually
    registered, so a deleted hook is a missing attribute rather than a silent no-op.
    """
    mounted = [
        module for module in list(sys.modules.values())
        if str(getattr(module, "__file__", "")).endswith(
            ("tests/conftest.py", "tests\\conftest.py"))
        and hasattr(module, "pytest_collection_modifyitems")]
    assert mounted, (
        "no loaded module exposes `pytest_collection_modifyitems` from "
        "tests/conftest.py — the citation WIRING GATE guard is UNWIRED, so a marker "
        "on the gate silences it while the suite reports green")
    reached: list[object] = []

    def recorder(items) -> None:
        # The REAL predicate is replaced so this witnesses the HOOK, not a copy of it.
        reached.append(items)
        raise pytest.UsageError("the wiring-gate guard was called")

    monkeypatch.setattr(_wiring_gate_marks, "refuse_suppressed_gates", recorder)
    marker = object()
    with pytest.raises(pytest.UsageError, match="the wiring-gate guard was called"):
        mounted[0].pytest_collection_modifyitems([marker])
    assert reached == [[marker]], (
        "the conftest hook is mounted but no longer calls "
        "`refuse_suppressed_gates` — the guard exists and nothing applies it")


def test_the_wiring_gate_guards_every_citation_gate() -> None:
    """A gate that is not in `GUARDED_ITEMS` is not guarded, so the SET is pinned.

    ⛔ MEASURED (review round 3): truncating `GUARDED_ITEMS` to one name — ONE line —
    and skip-marking the other gate left `62 passed, 1 skipped, exit 0`. Every new
    test iterated the constant, so none of them could see it go missing, and nothing
    outside `tests/_wiring_gate_marks.py` names these two gates. The gates are
    exactly this module's `test_the_citation_*` items, so the set is checked against
    what the module actually defines: a truncated set reds here, and so does a gate
    RENAMED out of the guarded set.
    """
    defined = {name for name in vars(sys.modules[__name__])
               if name.startswith("test_the_citation_")}
    assert defined == set(_wiring_gate_marks.GUARDED_ITEMS), (
        "`GUARDED_ITEMS` no longer matches the citation gates this module defines — "
        f"defined {sorted(defined)}, guarded "
        f"{sorted(_wiring_gate_marks.GUARDED_ITEMS)}. A gate missing from the set is "
        "a gate a marker can silence in one line")


def test_the_wiring_gate_refuses_a_gate_that_never_PASSED(tmp_path) -> None:
    """A skip in the BODY (or a fixture) carries no marker — the outcome half runs.

    Measured on this tree (review round 3): `pytest.skip()` as the first statement of
    the gate gave `62 passed, 1 skipped, exit 0`, and a module-level fixture that
    skips gave `63 skipped, exit 0`. Neither carries a marker, so the collection-time
    guard is blind to both by construction, and a gate that did not run certified
    nothing while the suite reported green.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import importlib.util\n"
        "import pytest\n"
        "\n"
        f"_spec = importlib.util.spec_from_file_location(\n"
        f"    '_wiring_gate_marks', {str(_WIRING_GATE_MARKS_SOURCE)!r})\n"
        "_wiring_gate_marks = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_wiring_gate_marks)\n"
        "\n"
        "def pytest_sessionstart(session):\n"
        "    _wiring_gate_marks.begin_session()\n"
        "\n"
        "def pytest_collection_modifyitems(items):\n"
        "    _wiring_gate_marks.refuse_suppressed_gates(items)\n"
        "\n"
        "def pytest_collection_finish(session):\n"
        "    _wiring_gate_marks.note_required(session.items)\n"
        "\n"
        "def pytest_runtest_logreport(report):\n"
        "    _wiring_gate_marks.note_report(report)\n"
        "\n"
        "def pytest_testnodedown(node, error):\n"
        "    _wiring_gate_marks.merge_worker(\n"
        "        (getattr(node, 'workeroutput', None) or {}).get(\n"
        "            'wiring_gate_marks'))\n"
        "\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    session.exitstatus = _wiring_gate_marks.refuse_unpassed(\n"
        "        session.config, exitstatus)\n"
        "    _wiring_gate_marks.end_session()\n",
        encoding="utf-8")
    name = sorted(_wiring_gate_marks.GUARDED_ITEMS)[0]
    target = tmp_path / _wiring_gate_marks.GUARDED_FILE
    run = [sys.executable, "-m", "pytest", str(target), "-q",
           "-c", str(tmp_path / "pytest.ini"), "-p", "no:cacheprovider"]

    target.write_text(
        f"import pytest\n\n\ndef {name}() -> None:\n"
        "    pytest.skip('probe: the gate certified nothing')\n",
        encoding="utf-8")
    skipped = subprocess.run(run, cwd=tmp_path, capture_output=True, text=True)
    assert skipped.returncode != 0, (
        "a gate that SKIPPED ITSELF was reported green: "
        + (skipped.stdout + skipped.stderr)[-1500:])
    assert "WIRING GATE did not PASS" in skipped.stderr + skipped.stdout, (
        "the outcome refusal did not name the gate: "
        + (skipped.stdout + skipped.stderr)[-1500:])

    # ...and the SAME module with a gate that runs PASSES, so the refusal above is
    # the skip and not the synthetic tree.
    target.write_text(
        f"def {name}() -> None:\n    assert True\n", encoding="utf-8")
    passed = subprocess.run(run, cwd=tmp_path, capture_output=True, text=True)
    assert passed.returncode == 0, (
        "an UNMARKED, PASSING gate was refused: "
        + (passed.stdout + passed.stderr)[-1500:])

    # ⛔ AND A PARTIAL RUN IS NOT A FALSE BLOCK: with the gate deselected by `-k` it
    # was never collected, so it is not `required` and the session stays green.
    deselected = subprocess.run(
        [*run, "-k", "not " + name], cwd=tmp_path, capture_output=True, text=True)
    assert deselected.returncode in (0, 5), (
        "`-k` deselection of the gate false-blocked the run: "
        + (deselected.stdout + deselected.stderr)[-1500:])

    # ⛔ NOR IS A COLLECT-ONLY RUN (review round 4, and this was a P0). `--collect-only`
    # has no call phase, so nothing CAN pass; before the bail, this red an invocation
    # that never claimed to run anything — and `tools/skip-guard.py --emit-manifest`
    # runs exactly this to build CI's coverage manifest and fails closed on any rc
    # other than 0/5, so it failed the manifest step of python-ci.yml,
    # post-merge-validation.yml, ci.yml and ci-timing.yml. Measured then: `65 tests
    # collected, EXIT=1` and `emit-manifest: collect-only did not COMPLETE`.
    target.write_text(
        f"import pytest\n\n\ndef {name}() -> None:\n"
        "    pytest.skip('probe')\n", encoding="utf-8")
    collected = subprocess.run(
        [*run, "--collect-only"], cwd=tmp_path, capture_output=True, text=True)
    assert collected.returncode == 0, (
        "a `--collect-only` run was RED, which fails closed CI's coverage-manifest "
        "generation: " + (collected.stdout + collected.stderr)[-1500:])

    # ⛔ A NESTED IN-PROCESS SESSION MAY NOT DISARM THE OUTER ONE (review round 5,
    # and this was a P1). With one process-global record, an inner
    # `pytest.main(["--co", ...])` as the gate's first statement ran
    # `sessionstart`'s reset over the OUTER session's record, so the outer outcome
    # check had nothing left to require: measured `64 passed, 1 skipped, EXIT 0` and
    # no refusal, in the SERIAL invocations CI really runs. The record is now a stack.
    target.write_text(
        f"import pytest\n\n\ndef {name}() -> None:\n"
        "    pytest.main(['--collect-only', '-q', '-p', 'no:cacheprovider'])\n"
        "    pytest.skip('probe: nested session, then a silent gate')\n",
        encoding="utf-8")
    nested = subprocess.run(run, cwd=tmp_path, capture_output=True, text=True)
    assert nested.returncode != 0, (
        "a nested `pytest.main()` disarmed the outer outcome check: "
        + (nested.stdout + nested.stderr)[-1500:])
    assert "WIRING GATE did not PASS" in nested.stdout + nested.stderr, (
        "the outer session did not name the gate after a nested session: "
        + (nested.stdout + nested.stderr)[-1500:])

    # ⛔ AND THE OUTCOME HALF SURVIVES `-n`, which is how CI runs the suite (review
    # round 4: measured green at `63 passed, 2 skipped, exit 0` before the worker
    # record was published to the controller — a worker runs a slice and the
    # controller collects nothing, so neither side could answer alone).
    pytest.importorskip("xdist")
    distributed_skip = subprocess.run(
        [*run, "-n", "2"], cwd=tmp_path, capture_output=True, text=True)
    assert distributed_skip.returncode != 0, (
        "a gate that skipped ITSELF was green under `-n 2`: "
        + (distributed_skip.stdout + distributed_skip.stderr)[-1500:])
    assert "WIRING GATE did not PASS" in (
        distributed_skip.stdout + distributed_skip.stderr), (
        "the distributed outcome refusal did not name the gate: "
        + (distributed_skip.stdout + distributed_skip.stderr)[-1500:])
