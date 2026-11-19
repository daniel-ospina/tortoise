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
import traceback
import types
from pathlib import Path

import pytest

from tools import session_retrofit_audit as audit
from tortoise import session_schema

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    "Pack-declared kinds validate against their own declared words, which become "
    "their pipeline stages rather than a wider status list (#11824).**")


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
    (measured: 0 of 42 headers carry one), so the wider extent changes no verdict
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
    pattern counts that quote as a release — measured at this head: 43 pieces
    for 42 blocks, one of them a fragment starting at the quoted header.
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
    revert, because 0 of 42 headers carry a citation at this head, so every
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
        "a block's HEADER line now carries a comment citation, so the `0 of 42 "
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
        "Pack-declared kinds validate against their own declared words, which become "
        "their pipeline stages rather than a wider status list (#11824).**"), (
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

    ``docs/ONTOLOGY.md:41`` and the changelog's own CRITICAL RULE both require
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
    declared words, which become their pipeline stages rather than a wider status
    list (#11824).**"*). It is NOT "§4.3 may not change": every other cell of
    that same row, and every other row of §4.3, is INVISIBLE to this comparison —
    a lane can rewrite the cell's opening sentence or the ``supersededBy`` row and
    this pin stays green, while ``docs/ONTOLOGY.md:41``'s CRITICAL RULE requires
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
    question — cites none"* as the evidence for narrowing a gate's scope. Measured with
    this module's own tools the count is **7 of 43**, and that block DOES cite — the permalink
    ``#issuecomment-5619843002`` (v3.33's issue-#11824 block, ``docs/ONTOLOGY-CHANGELOG.md:406``):
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
    # what it does (measured: re-wrapping `**7 of 43**\n blocks cite a comment id`
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
    # complement when `k + 1 == n - k`, i.e. `2k + 1 == n` — a collision only an ODD
    # block count can produce. With 42 blocks and 6 citing, `k + 1` cannot collide
    # with the complement — `2·6 + 1 = 13 ≠ 42` — so
    # the fixture below is a deliberate construction rather than a state this file
    # reaches; what it pins is that the arm rejects a stale numerator in EITHER
    # direction, which is as true of a hand-built pair as of a real one.
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
    still recorded and still neutralised (measured at ``9828250732``: that shape alone
    left the file at 59 passed, while the other four inert-call shapes #12223 lists
    redded it at ``1 failed, 58 passed``). ⛔ THAT SHAPE IS NOW DECIDED, and NOT by
    reading source — which is what this paragraph used to claim the remedy would
    require: ``test_the_citation_pin_propagates_every_planted_defect`` plants the defect
    in the pin's INPUT and requires the pin to FAIL, so a swallowed refusal reds an
    OUTCOME assertion. Measured with that probe added at the same base: the swallowing
    shape now reds this file at ``1 failed, 59 passed``, together with a swallow at each
    of the pin's other four call sites.
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
    # docstring claiming 36 of 42 blocks cite a comment id (6 do) would pass it.
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
        # complement in ANY position, so changing this docstring's `count is **7 of 43**`
        # to `**36 of 43**` — a false claim, since seven blocks cite — left the
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



def test_the_citation_pin_propagates_every_planted_defect(monkeypatch, tmp_path) -> None:
    """Each arm's REFUSAL must escape the pin — a recorded call is not enough.

    ⛔ THE THIRD HALF of this pin's own three guards, and the one #12223 measured
    missing. ``test_every_declared_sweep_arm_refuses_a_stale_restatement`` drives the
    arms from synthetic text (the ARM works); ``test_the_citation_pin_calls_every_arm_it_declares``
    records the arguments the pin passes (the pin REACHES each arm with
    the right subject). Neither decides whether a reached arm still REFUSES anything in
    the pin's own run: an arm wrapped in ``try: … except AssertionError: pass`` is
    reached, records its arguments, and is neutralised. Measured on the base this
    change builds on (``9828250732``, 59 tests) — that shape left the file at **59
    passed**, while the other four inert-call shapes #12223 lists (dead branch, rebound
    argument, never-called nested ``def``, never-evaluated comprehension) all redded
    it. So the value-recording cut closed four of the five, and this test closes the
    fifth.

    ⛔ HOW IT DECIDES, and why this does not reintroduce source reading: the defect is
    planted in the pin's INPUT and the pin must FAIL — the mutation-testing question
    ("is the mutant killed?"), answered from the pin's own OUTCOME. A surviving mutant
    here is a false PASS. ⛔ WHAT THAT DOES AND DOES NOT MEAN, stated exactly: no VERDICT
    is derived from the source's shape — the one observation is whether a refusal
    ESCAPES the pin, which is why the mechanism this file was rewritten to stop
    relying on stays gone. The PLANTS, however, are built as text (a plant is appended
    to the module source, and ``repointed`` finds a docstring's permalinks by regex —
    which is exactly why the permalink FORM is named there), and the plant set is an
    ENUMERATION of the five call sites, so a sixth call site added later is not
    covered — the last paragraph says so rather than implying otherwise. A cut of this
    docstring claimed "nothing is parsed as text and no call site is enumerated" and
    was false on both halves.

    ⛔ ONE PLANT PER CALL SITE — the pin makes FIVE, not three: the module-wide
    permalink call, TWO ``carrier=`` calls (the sibling docstring's and this pin's
    own), the uniqueness scan, and the stale-restatement sweep. The two carrier calls
    are separate sites and each needs its own plant, because a ``try/except`` around
    one of them leaves the other to refuse nothing while the suite stays green
    (measured on the previous cut of this probe: 60 passed with the sibling's carrier
    call swallowed). The plants are, in that order: a docstring citing a permalink no
    block carries; each named docstring REPOINTED at a real id another block carries;
    a second copy of the bold count pair; an ordinal sentence naming the lander with
    the WRONG ordinal.

    ⛔ THE SEAM IS TWO SEAMS, and they are not interchangeable. ``module_source`` and
    ``prose`` are read from ``Path(__file__)``, so a planted copy reached by pointing
    that name at it exercises them. The carrier arms' subject is a LIVE ``__doc__``
    ATTRIBUTE, which no ``__file__`` plant can reach — measured, a carrier defect
    delivered through ``__file__`` alone leaves the pin PASSING, so that plant would
    appear to cover the carrier arm and measure nothing. Each carrier plant therefore
    also patches the attribute (``<function>.__doc__``). ``Path.read_text`` is
    deliberately NOT patched: it is process-global, so it would also intercept the
    changelog read and every other read in the pin's call graph.

    ⛔ ISOLATION IS MECHANICAL, NOT PROSE. For each probe the OTHER arms are replaced
    by pass-throughs, so "only this arm can refuse the plant" is enforced rather than
    asserted in a comment — and no probe depends on an arm's message WORDING, so a
    legitimate reword cannot red this test while nothing is wrong.

    ⛔ THE CONTROL RUN IS PART OF THE TEST: the pin is called once on the REAL inputs
    and must NOT raise. Without it a probe that raises for an unrelated reason would
    satisfy every ``pytest.raises`` below, and the whole test would report success
    while measuring nothing.

    ⛔ WHAT THIS STILL CANNOT DECIDE, stated rather than implied: it proves a refusal
    escapes the pin for the defect PLANTED, not that every refusal path is unwrapped.
    A ``try/except`` around a refusal no plant reaches survives, and the plant set
    above IS the coverage — it covers all five call sites the pin makes, and nothing
    else. It also cannot see an edit that removes an assertion from both the pin and
    this test, which is the class no in-file test can decide.
    """
    pin = test_the_citation_scope_docstring_states_the_measured_count
    module = sys.modules[__name__]
    sibling = test_the_newest_block_recording_4_3_cites_a_comment_id
    blocks = _block_bodies(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    citing = [block for block in blocks if _CITATION_RE.search(block)]
    lands_d2 = [block for block in blocks if "D2 " + "— A" in block]
    v333 = [block for block in blocks
            if _BLOCK_VERSION_RE.match(block).group(1) == "3.33"]
    assert len(lands_d2) == 1 and v333, (
        "the changelog no longer records one lander among the v3.33 entries, so the "
        "ordinal plant below cannot be built — re-read this probe before dropping it")
    lands_first = lands_d2[0] == v333[0]
    wrong_ordinal = "second" if lands_first else "first"
    lander_ids = set(_citation_ids(lands_d2[0]))
    elsewhere = [identifier for block in blocks if block != lands_d2[0]
                 for identifier in _citation_ids(block)
                 if identifier not in lander_ids]
    assert elsewhere, (
        "no block other than the lander carries a comment id, so the carrier plants "
        "cannot be built — re-read this probe before dropping it")
    # ⛔ EVERY PLANTED NUMBER IS COMPUTED, never spelled. The practice is right but
    # the earlier reason given for it was an over-claim, corrected here after
    # measurement: a plain UNBOLDED count in this docstring is not read by any arm at
    # all, and a bold pair carrying the CORRECT measurement passes ``allowed_bold``.
    # A bold STALE one reds the pin (measured), and that is the case computing avoids.
    real_arm = module._refuse_unresolvable_evidence

    def module_wide_only(prose: str, block_texts: list[str], *,
                         carrier: str | None = None) -> None:
        """Pass the module-wide call through; the carrier calls are inert."""
        return None if carrier is not None else real_arm(prose, block_texts)

    def carrier_only(prose: str, block_texts: list[str], *,
                     carrier: str | None = None) -> None:
        """Pass a carrier call through; the module-wide call is inert."""
        return None if carrier is None else real_arm(
            prose, block_texts, carrier=carrier)

    def inert(*args: object, **kwargs: object) -> None:
        return None

    def sweep_inert(*args: object, **kwargs: object) -> list[str]:
        """Truthy, because the pin requires the module to name the lander's ordinal."""
        return ["first" if lands_first else "second"]

    def repointed(doc: str) -> str:
        """The same docstring, citing a real id that belongs to ANOTHER block.

        ⛔ THE PERMALINK FORM SPECIFICALLY. The carrier arm reads
        ``#issuecomment-<id>`` only, so repointing an id that appears solely in the
        inline ``comment <id>`` form changes nothing it reads — measured on the first
        cut of this probe: the sibling's docstring cites four ids and its FIRST is an
        inline one, so replacing that one left the carrier arm passing and this probe
        reporting a defect it had not planted.
        """
        cited = re.findall(r"#issuecomment-(\d{6,})", doc)
        assert cited, "the docstring this probe repoints cites no permalink"
        return doc.replace("#issuecomment-" + cited[0],
                           "#issuecomment-" + elsewhere[0], 1)

    source = Path(__file__).read_text(encoding="utf-8")
    carrier_arms = {"_refuse_unresolvable_evidence": carrier_only,
                    "_refuse_a_duplicated_statement": inert,
                    "_assert_no_stale_citation_claim": sweep_inert}
    plants = (
        (
            "the module-wide permalink arm", "_refuse_unresolvable_evidence",
            source + "\n\ndef _planted_unresolvable_permalink() -> None:\n"
            '    """Planted defect: ``#issuecomment-'
            + "9" * 10 + "`` resolves to no changelog block.\"\"\"\n",
            None,
            {"_refuse_unresolvable_evidence": module_wide_only,
             "_refuse_a_duplicated_statement": inert,
             "_assert_no_stale_citation_claim": sweep_inert},
        ),
        (
            "the sibling docstring's carrier arm", "_refuse_unresolvable_evidence",
            source, (sibling, repointed(sibling.__doc__ or "")), carrier_arms,
        ),
        (
            "this pin's own carrier arm", "_refuse_unresolvable_evidence",
            source, (pin, repointed(pin.__doc__ or "")), carrier_arms,
        ),
        (
            "the uniqueness scan over the count pair", "_refuse_a_duplicated_statement",
            source + "\n\n_PLANTED_DUPLICATE = "
            f"'**{len(citing)} of {len(blocks)}** blocks cite a comment id'\n",
            None,
            {"_refuse_unresolvable_evidence": inert,
             "_assert_no_stale_citation_claim": sweep_inert},
        ),
        (
            "the stale-restatement sweep's ordinal arm", "_assert_no_stale_citation_claim",
            source + "\n\n_PLANTED_ORDINAL = "
            f"'the {wrong_ordinal} of the two v3.33 entries landed `D2 " + "— A`'\n",
            None,
            {"_refuse_unresolvable_evidence": inert,
             "_refuse_a_duplicated_statement": inert},
        ),
    )
    pin()
    for index, (label, target_arm, patched_source, live_doc, arms) in enumerate(plants):
        target = tmp_path / f"planted_{index}.py"
        target.write_text(patched_source, encoding="utf-8")
        with monkeypatch.context() as patch:
            patch.setattr(module, "__file__", str(target))
            for name, replacement in arms.items():
                patch.setattr(module, name, replacement)
            if live_doc is not None:
                holder, text = live_doc
                patch.setattr(holder, "__doc__", text)
            with pytest.raises(AssertionError) as raised:
                pin()
            # ⛔ THE RAISE MUST COME OUT OF THE TARGET ARM, not merely happen. Measured
            # on the previous cut of this probe: swallowing the SWEEP call as
            # `except AssertionError: ordinals = []` left the pin's own downstream
            # `assert ordinals` to raise instead, so a bare `pytest.raises` reported
            # success while the swept arm had refused nothing — 60 passed, a surviving
            # mutant. The frame name is read from the traceback, so no arm's message
            # wording is involved and a legitimate reword cannot red this test.
            frames = {frame.name for frame in traceback.extract_tb(raised.tb)}
            assert target_arm in frames, (
                f"{label}: the pin raised, but not out of {target_arm} — a neighbouring "
                "check fired instead (the pin's own downstream assertion, or another "
                f"arm), so this probe measured nothing. Frames seen: {sorted(frames)}")
        assert module.__file__ != str(target), (
            f"{label}: the planted `__file__` outlived its probe, so the control run "
            "above no longer describes the pin this suite ships")



def test_the_newest_block_recording_4_3_cites_a_comment_id() -> None:
    """A §4.3-recording release must cite a comment id, in one of the enumerated forms.

    The file's CRITICAL RULE names a PERSON, so the entry has to name where that
    person's answer lives. The sibling entries do it by comment id —
    ``#11827 (comment 6805834755)``, ``#11083 (comment 6667121779)`` — and the
    v3.34 entry did not: it asserted an owner approval whose only home was a
    decision lane's pane, so it named no surface the next lane could read
    (``DECISION RELAY``: recorded nowhere the next lane reads is a default, not a
    decision). The relay comment that finally recorded it
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
    the file's practice does not support the demand: **7 of 43** blocks cite a
    comment id, so **36 of 43** cite none; citing versions: v3.38, v3.37, v3.36, v3.34, v3.33, v3.33, v3.32
    — one entry per block, the first of the two v3.33 entries being the block
    that landed `D2 — A`. An unconditional demand reds a legitimate release that
    changes something else. (Measured with this module's own ``_block_bodies`` and
    ``_CITATION_RE`` at the current head of the changelog — no commit stamp, because a
    count restamped to a past head is a measurement attributed to a file state that
    cannot contain it; the counts and versions stated here are that measurement, and they are stated
    ONCE — the citation pin ABOVE refuses a duplicate. (What that arm buys, precisely: a
    second copy in BOLD is swept against the measurement and a second copy of a
    PATTERNED sentence is refused by shape, while a PLAIN restatement — "Only 3 of
    39 blocks cite a comment id." — is prose no pin reads, which is why the
    patterned sentences are held to one copy each.) An earlier cut of this
    sentence said "3 of 39" and added that
    v3.33 — the block that landed `D2 — A` for this very question — *cites none*:
    BOTH halves were wrong. That block is `docs/ONTOLOGY-CHANGELOG.md:406` (issue
    #11824) and it DOES cite, as the permalink ``#issuecomment-5619843002`` — an
    accepted form, enumerated below, in the sentence the old text contradicted.
    So v3.33 is not evidence for the narrow scope; the 36 blocks that
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
    # uses that style (the v3.32 block): accepting it
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


# ⛔ THE TWO SHAPES THE v3.38 RELEASE SHIPPED AND A REVIEW CAUGHT, kept verbatim as the
# NON-VACUITY direction below. Both are PROSE, and the §4.3 pin above is scoped away
# from every release that does not quote that clause — which is why these shipped
# through the four review rounds the PR body says caught them. An arm scoped by
# CONTENT cannot guard an entry whose content is something else; these two read what
# the entry actually claims about its own evidence.
_PREFIX_APPROVAL_RECORD = (
    "> **Owner approval of the exact change:** 2026-11-18, given in-session, after the audit\n"
    "> measured the behaviour. The artifact is the text itself.")
# ⛔ THE SAME RECORD IN THE TWO SHAPES A COPY-EDIT PRODUCES, each measured FAIL-OPEN
# (review @ e4927f5f): a doubled space after `Owner`, and markdown emphasis on
# `approval` — or the whole label in `__…__` — all RENDER as the same label, and each
# escaped a literal `\*\*Owner approval` search, so a record with NO home passed.
_PREFIX_APPROVAL_RECORD_WITH_A_DOUBLED_SPACE = (
    "> **Owner  approval of the exact change:** 2026-11-18, given in-session, after the\n"
    "> audit measured the behaviour. The artifact is the text itself.")
_PREFIX_APPROVAL_RECORD_WITH_EMPHASIS_IN_THE_LABEL = (
    "> **Owner *approval* of the exact change:** 2026-11-18, given in-session, after the\n"
    "> audit measured the behaviour. The artifact is the text itself.")
_PREFIX_APPROVAL_RECORD_IN_UNDERSCORE_EMPHASIS = (
    "> __Owner approval of the exact change:__ 2026-11-18, given in-session, after the\n"
    "> audit measured the behaviour. The artifact is the text itself.")
# ⛔ AND THE SAME LABEL IN ANOTHER CASING, measured ACCEPTED (review @ 95a8df2f):
# `**Owner Approval**` and `**OWNER APPROVAL**` render as the label, and a case-sensitive
# search read neither, so a record with no home passed in both.
_PREFIX_APPROVAL_RECORD_IN_TITLE_CASE = (
    "> **Owner Approval of the exact changes:** 2026-11-18, given in-session, after the\n"
    "> audit measured the behaviour. The artifact is the text itself.")
_PREFIX_APPROVAL_RECORD_IN_ALL_CAPS = (
    "> **OWNER APPROVAL of the exact changes:** 2026-11-18, given in-session, after the\n"
    "> audit measured the behaviour. The artifact is the text itself.")
# ⛔ AND THE MIRROR DIRECTION (review @ 95a8df2f): an id that follows an ABBREVIATION in
# the label sentence is still in that sentence to a reader, so it must resolve. Before
# the splitter stopped treating `e.g.`'s final dot as a terminator the arm refused it.
_APPROVAL_CITING_AFTER_AN_ABBREVIATION = (
    "> **Owner approval of the exact changes:** given in-session on 2026-11-18, e.g. as\n"
    "> comment `6993778525`, which the reader can resolve.")
# ⛔ THE BOLDED LABEL WITH NO COLON, driven because the arm was measured FAIL-OPEN on it
# (review @ 9d34e510, finding 1): the detector required ``…changes:`` — phrase AND colon —
# so the em-dash form and the ``is on record`` form were ACCEPTED with no home at all.
# Five of the seven landed records write one of these two shapes. BOLD is the label's
# boundary; the punctuation after it is prose.
_PREFIX_APPROVAL_RECORD_IS_ON_RECORD = (
    "> **Owner approval of the exact changes** is on record. The artifact is the text\n"
    "> itself.")
# ⛔ AND THE LABEL RUN **CUT BY** THE SENTENCE END (review of #13679, finding 2). The
# paragraph detector sees a run, but the splitter cuts the period INSIDE it, so neither
# sentence carries a CLOSED run and the label lookup returns `None`. This is the only shape
# that drives `assert label is not None`: with the phrase-only selector both sentences
# matched, the citation assert fired instead, and the guard could not be reddened by any
# mutant of this diff.
_APPROVAL_LABEL_STRADDLING_A_SENTENCE_END = (
    "> **Owner approval of the exact changes. The rest runs on** and names no id.")
# ⛔ THE MID-PARAGRAPH LABEL IS THE DECLARED BOUNDARY OF THIS DETECTOR, DRIVEN BOTH WAYS
# (merge reconciliation with `main`'s #13679, whose paragraph-wide scan read these). This
# reader only reads a label that OPENS its paragraph, so both fixtures PASS — and that is
# the residual list's third bullet, not an oversight: the same restraint is what keeps a
# bolded MID-SENTENCE mention (``No **owner approval** was required``) from becoming a
# record, which is a false block on prose the file actually writes. Both directions are
# pinned so a later widening reds here and has to meet the trade rather than assume it.
_APPROVAL_LABEL_AFTER_AN_UNBOLDED_MENTION = (
    "> Following the owner approval relayed earlier, the wording was corrected. **Owner\n"
    "> approval of the exact changes:** the ruling is at comment `6993778525`.")
# …and the fail-OPEN side of the same boundary: a record whose label is mid-paragraph and
# whose citation sits in a NEIGHBOURING sentence PASSES. `main`'s paragraph-wide scan
# refused it; this detector does not, and the narrowing is stated in the residual list and
# driven here so it is a measurement rather than a claim.
_PREFIX_APPROVAL_RECORD_WITH_AN_UNBOLDED_MENTION_CITING_AN_ID = (
    "> The owner approval for v3.31 was relayed at comment `6912345`. **Owner approval\n"
    "> of the exact changes** is on record. The artifact is the text itself.")
_PREFIX_UNRESOLVED_POINTER = (
    "> reads four conditions). That is a code defect with its own issue; it is not a "
    "sentence to rewrite.")
# ⛔ THE SAME POINTER, SOFT-WRAPPED, and it is a fixture because the arm was
# measured FAIL-OPEN on this shape (review @ 30ef349c, finding P2): the arm split the
# RAW block, so ``own`` and ``issue`` on separate physical lines never met and the
# sentence that names a home it cannot give was never examined. A soft wrap is not a
# semantic boundary — the entry it guards reflows every release.
_POINTER_SPLIT_BY_A_SOFT_WRAP = (
    "> reads four conditions). That is a code defect with its own\n"
    "> issue; it is not a sentence to rewrite.")
# ⛔ AND THE SECOND HALF, DRIVEN SEPARATELY SO THE TWO FIXES CANNOT SHADOW EACH OTHER:
# the pointer sentence is closed by a QUOTATION (``itself."``), so a splitter keyed on
# ``(?<=[.;!?])`` sees the borrowed ``#13450`` in the NEXT sentence as part of this one
# and the pointer passes. Measured GREEN before the fix, with the wrap fix alone still
# GREEN — they are two independent fail-opens and both are needed.
_POINTER_BORROWING_AN_ID_ACROSS_A_QUOTE_BOUNDARY = (
    '> The ruling reads "the artifact is the text itself."* That is a code defect\n'
    '> with its own issue."  Citing #13450 does not change it.')
# ⛔ THE TWO SHAPES A COPY-EDIT PRODUCES, each measured FAIL-OPEN (review @ 30ef349c,
# round 2): the phrase is what the DOCUMENT RENDERS, so a doubled space and a markdown
# emphasis run are the same pointer as far as a reader is concerned — and neither
# matched ``\bown issue\b``, so the arm slept through both.
_POINTER_WITH_A_DOUBLED_SPACE = (
    "> reads four conditions). That is a code defect with its own  issue; it is not a\n"
    "> sentence to rewrite.")
_POINTER_WITH_AN_EMPHASIS_RUN = (
    "> reads four conditions). That is a code defect with its own *issue*; it is not a\n"
    "> sentence to rewrite.")
# ⛔ AND THE PARAGRAPH BOUNDARY, which is why the run is bounded per PARAGRAPH: this
# pointer ends without terminal punctuation, so unfolding the whole block glued the
# next paragraph's id onto it and the pointer passed on a number a reader would never
# read as its own (measured GREEN). The next paragraph's ``#13450`` is a DIFFERENT
# sentence by this arm's own stated scope.
_POINTER_ACROSS_A_PARAGRAPH_BREAK = (
    "> reads four conditions). That is a code defect with its own issue\n"
    ">\n"
    "> see #13450 for the defect it names.")
# ⛔ ORDINARY COPY-EDITS OF THE PHRASE ITSELF, each measured ACCEPTED (review @ 95a8df2f):
# `its own GitHub issue`, `its own follow-up issue`, `its own issues`, `own Issue`. A
# reader sees the same pointer in every one, and the arm slept through all of them
# because the phrase was the adjacency-exact `\bown\s+issue\b` in one case.
_POINTER_WITH_A_QUALIFIED_ISSUE = (
    "> reads four conditions). That is a code defect with its own GitHub issue; it is "
    "not a sentence to rewrite.")
_POINTER_IN_THE_PLURAL = (
    "> reads four conditions). That is a code defect with its own issues; it is not a "
    "sentence to rewrite.")
_POINTER_CAPITALISED = (
    "> reads four conditions). That is a code defect with its own Issue; it is not a "
    "sentence to rewrite.")
# ⛔ THE PHRASE IN THE OTHER WORD ORDER, measured ACCEPTED (review @ 9d34e510, finding 2):
# ``an issue of its own`` is the same pointer as ``its own issue`` — a reader follows
# either to nothing — and the regex knew one direction only. The fixture kept from that
# round is the MIRROR: resolved prose in that order must stay green. (The two UNRESOLVED
# ones this round added are `_POINTER_IN_THE_OTHER_WORD_ORDER` and its qualified sibling,
# which is this file's pair.)
_POINTER_RESOLVED_WITH_THE_ISSUE_FIRST = (
    "> reads four conditions). That is a code defect with an issue of its own, #13450; it "
    "is not a sentence to rewrite.")
# ⛔ AND THE MIRROR DIRECTION THE SPLITTER WAS GETTING WRONG (review @ 95a8df2f): `;` is
# NOT a sentence boundary a reader sees, so a resolved pointer after one is ONE sentence
# and must stay GREEN. The arm refused it — a false block on true prose, the same
# pressure that produced the §4.3 pin's measured 36/43 false-block.
_POINTER_RESOLVED_AFTER_A_SEMICOLON = (
    "> reads four conditions). That is a code defect with its own issue; see #13450 for "
    "the defect it names.")
# ⛔ THE SHAPE THE ARM MUST IGNORE (review F1), kept as a CONTROL: a release that records
# NO owner approval owes no citation. The first cut asserted `len(records) == 1`, which
# fires on ZERO as well, so it refused the 37 of the 43 landed blocks that carry no
# record at all (re-derived at this head, where the arm's own numbers are pinned: 43
# blocks, 6 carrying exactly one record to the SHIPPED detector) — the same
# measured false-block the §4.3 pin above rejects, reintroduced under a new predicate.
# ⛔ AND THIS CONTROL IS HONEST ABOUT WHY IT IS GREEN (review @ 95a8df2f, then #13669):
# its negative sentence's paragraph OPENS with a bold *changelog header*, not an approval
# label, which is what makes it structurally not a record. The colon the shipped detector
# demanded was the accident that used to do this job — that detector was already
# case-insensitive, so casing decided nothing. `_NO_APPROVAL_RECORD_IN_PROSE` drives the
# sentence-initial capital, and `_NO_APPROVAL_RECORD_IN_BOLD_PROSE` the bolded mid-sentence
# denial, so neither direction is left to an accident.
_NO_APPROVAL_RECORD = (
    "> **Changelog v3.39 (2026-11-19 — a wording fix in §4.5 that records no approval):**\n"
    ">\n"
    "> **What changed.** §4.5's table gained a row and its prose said the same thing "
    "more plainly. No owner approval was required for a wording fix.")
_NO_APPROVAL_RECORD_IN_PROSE = (
    "> **Changelog v3.39 (2026-11-19 — a wording fix that records no approval):**\n"
    ">\n"
    "> Owner approval was not required for a wording fix.")
# ⛔ AND THE NEGATIVE SENTENCE WITH A COLON (review @ 9d34e510, finding 5): the cut that
# required the colon claimed the colon was what kept "no approval was recorded" out of the
# detector. This fixture HAS one and records no approval, so the boundary is BOLD, not the
# colon — the claim was false, and the shape is driven here so the fix is a measurement.
_NO_APPROVAL_RECORD_WITH_A_COLON = (
    "> **Changelog v3.39 (2026-11-19 — a wording fix that records no approval):**\n"
    ">\n"
    "> No owner approval was required for a wording fix: the change is trivial.")
# ⛔ THE F2 FAIL-OPEN, driven as a fixture (review: "needs its own fixture (an id that is
# NOT the approval's)"). This is the landed v3.38 record with its OWN relay deleted: the
# only id left is the incidental one the next sentence cites, for a comment about the
# form the owner REJECTED. A paragraph-wide search is satisfied by it; a sentence-wide
# one is not, because an id in a later sentence belongs to what that sentence says.
_APPROVAL_CITING_A_LATER_SENTENCE = (
    "> **Owner approval of the exact changes:** the owner ruled on **2026-11-18, "
    "in-session**.\n"
    "> Citing the comment is what lets a reader resolve the approval — the form the "
    "owner\n"
    "> rejected for v3.36 (comment `6965147045`).")
# …and the SAME fail-open with the sentence closed by a QUOTATION, which is the shape
# v3.38 actually ships (``… is a stale sentence."* Citing the comment …``). It is a
# second fixture on purpose: a splitter keyed on the character immediately before the
# space (``(?<=[.;!?])\s+``) sees ONE sentence here, so the borrowed id passes —
# measured, the naive splitter left this direction GREEN. The boundary has to include
# the closing quote/emphasis run.
_APPROVAL_CITING_A_LATER_SENTENCE_AFTER_A_QUOTATION = (
    "> **Owner approval of the exact changes:** the owner ruled on **2026-11-18, "
    "in-session**: *\"the artifact is the text itself.\"*\n"
    "> Citing the comment is what lets a reader resolve the approval — the form the "
    "owner\n"
    "> rejected for v3.36 (comment `6965147045`).")
# …and the RESOLVED direction: the label's own sentence carries the relay, and the
# incidental reference sits in the next sentence. Green here is what makes the fixture
# above a measurement rather than a blanket refusal of every record that names an id.
_APPROVAL_RELAY_IN_LABEL_SENTENCE = (
    "> **Owner approval of the exact changes:** the owner ruled on **2026-11-18, "
    "in-session**,\n"
    "> and the ruling is relayed onto **#11824** as comment `6993778525`: *\"a stale "
    "sentence.\"*\n"
    "> The contrast that follows is its own sentence — the form the owner rejected for\n"
    "> v3.36 (comment `6965147045`).")
# ⛔ THE TWO LANDED SHAPES THE SHIPPED DETECTOR COULD NOT SEE (#13669 finding 1). Both
# are the file's OWN practice and both passed while the arm required a colon inside the
# label: the first is the bold label with the citation in the NEXT sentence — the exact
# form v3.37 rejected as unresolvable, written without a colon — and the second is the
# landed v3.32 shape moved onto an ISSUE reference by deleting its comment id.
_APPROVAL_LABELLED_WITHOUT_A_COLON = (
    "> **Owner approval of the exact changes** — given in-session on 2026-11-18, after "
    "the\n"
    "> audit measured the behaviour. The artifact is the text itself.")
_APPROVAL_CITING_AN_ISSUE_NOT_A_COMMENT = (
    "> **Owner approval of the exact changes is on record on #11083** — the ruling was\n"
    "> given in-session, 2026-11-05.")
# ⛔ THE LANDED v3.33 LABEL (#13669 finding 1, third case): the corpus states approval in
# a bold ``**Approval**`` run that never says "owner approval", so the shipped detector
# never examined it at all. It is a record this arm now REFUSES — a false block on true
# prose, disclosed in the arm's docstring — and it is a fixture so the refusal is a
# measured direction rather than a claim.
_APPROVAL_IN_A_BARE_BOLD_LABEL = (
    "> **Approval** is the owner's decision D2 — A on #11824 (2026-11-11): core Object\n"
    "> types get their own lifecycle vocabulary, validated per kind.")
# ⛔ THE NEGATIVE EVIDENCE, STATED IN BOLD MID-SENTENCE (#13669 finding 5). The shipped
# detector REFUSED this — it found `owner approval` and demanded a citation — while the
# comment beside it claimed the colon kept denials out. The structural read excludes it
# because a denial does not OPEN the paragraph; the fixture pins that direction.
_NO_APPROVAL_RECORD_IN_BOLD_PROSE = (
    "> **Changelog v3.39 (2026-11-19 — a wording fix that records no approval):**\n"
    ">\n"
    "> A wording fix landed. No **owner approval** was required for it: the change is\n"
    "> trivial.")
# ⛔ THE LABEL'S SENTENCE ENDS AT A FULL STOP (#13669 finding 3): the shipped cut located
# the label sentence with `next(...)` and no default, so this shape raised a bare
# `StopIteration` — an uncaught non-AssertionError that `_assert_refused_by` cannot
# classify, so a lane saw a traceback and no diagnosis. The refusal must be an
# AssertionError with a message, which the direction below measures.
_APPROVAL_LABEL_SENTENCE_ENDS_AT_A_FULL_STOP = (
    "> **Owner approval of the exact changes.** Given in-session: comment `6993778525`.")
# ⛔ THE POINTER IN THE OTHER WORD ORDER (#13669 finding 2): `an issue of its own` reads
# as the same pointer to a reader and the shipped adjacency-exact pattern (`own` …
# `issue`) missed it, so the defect it names was tracked by nothing.
_POINTER_IN_THE_OTHER_WORD_ORDER = (
    "> reads four conditions). That is a code defect with an issue of its own; it is "
    "not\n"
    "> a sentence to rewrite.")
_POINTER_QUALIFIED_IN_THE_OTHER_WORD_ORDER = (
    "> reads four conditions). That is a code defect with a tracking issue of its own.")
# ⛔ THE TWO SUB-PATTERNS BESIDE THE STOP-LIST HAD NO FIXTURE (review @ 764b5cf2, finding
# 1, P2): the reversed form admits ``of`` and ``all of``, and a window of up to TWO words
# before ``own`` — and deleting EITHER left all 62 rows green while these two sentences went
# REFUSED -> ACCEPTED. They are the same no-home pointer the arm exists to refuse, one
# copy-edit away from the shipped fixture, so each alternative is driven here by name.
_POINTER_WITH_AN_ALL_OF_IDIOM = (
    "> reads four conditions). That is a code defect with an issue all of its own.")
_POINTER_WITH_A_TWO_WORD_WINDOW = (
    "> reads four conditions). That is a code defect with an issue of their very own.")
# ⛔ THE SAME RECORD BEHIND A LIST MARKER, OR IN SINGLE-MARKER EMPHASIS (review round 2,
# all four measured FAIL-OPEN): a changelog entry may state its label as a bullet, an
# ordered item, `*…*` or `_…_`, and a detector anchored on `**`/`__` at the paragraph's
# first byte slept through every one — the arm was silent on a record it exists to
# refuse.
_APPROVAL_RECORD_AS_A_BULLET = (
    "> **Changelog v3.39 (2026-11-19 — a wording fix):**\n"
    ">\n"
    "> - **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_AS_AN_ORDERED_ITEM = (
    "> **Changelog v3.39 (2026-11-19 — a wording fix):**\n"
    ">\n"
    "> 1. **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ …AND THE CITED FORM, kept as the ACCEPT direction (review round 6): the ordered
# marker ends in a full stop, so the label sentence used to be cut down to `1.` and this
# record — whose citation IS in the sentence — was refused. `- `, `- [ ] `, `### ` and
# `1) ` never had the problem; only the `.`-terminated ordered marker (`\d+\.`) did.
# (Review round 7 corrected this line: it listed `1)` as never-cut two words before naming
# `\d+[.)]` as the family — `1)` is a member of that family, and `_sentences` returns ONE
# sentence for it; the cut comes from the marker's full stop alone.)
_APPROVAL_RECORD_AS_A_CITED_ORDERED_ITEM = (
    "> 1. **Owner approval of the exact changes**: comment `6993778525`.")
# ⛔ THE REMAINING CONTAINERS (review round 6), each measured ACCEPTED when its
# alternative is dropped while the whole suite stays green — so each is driven here.
_APPROVAL_RECORD_AS_AN_ASTERISK_BULLET = (
    "> * **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_AS_A_PLUS_BULLET = (
    "> + **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_AS_A_PARENTHESISED_ITEM = (
    "> 1) **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_BEHIND_A_CHECKED_TASK_LIST_BOX = (
    "> - [x] **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ AND THE CAPITAL BOX (review round 9): `[X]` renders identically to `[x]` and had no
# fixture, so narrowing `[ xX]` to `[ x]` left the suite GREEN while that label went unread.
_APPROVAL_RECORD_BEHIND_A_CAPITAL_X_TASK_LIST_BOX = (
    "> - [X] **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ THE ITEM THAT IS NOT FIRST (review round 5, measured FAIL-OPEN and a REGRESSION from
# `main`): a compact list carries no `>` separator between its items, so joining the block
# by line fused every item into ONE paragraph and the marker strip could only reach the
# FIRST one. A record written as a later bullet was therefore never read, while `main`'s
# phrase detector refused it — the arm did not reduce the vacuity here, it widened it.
# The corpus cannot see this (nothing landed records an approval inside a list), so the
# direction is pinned by fixture; `docs/ONTOLOGY-CHANGELOG.md` has 111 lines that begin
# with a list marker after the quote prefix (93 bullets, 18 ordered, one a nested
# continuation) and
# its lists are compact, so this is the shape the NEXT release reaches.
_APPROVAL_RECORD_AS_A_LATER_BULLET = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> - **Owner approval of the exact changes:** given in-session, no comment cited.")
_APPROVAL_RECORD_AS_A_LATER_ORDERED_ITEM = (
    "> 1. Retired the legacy predicate in §4.5.\n"
    "> 2. **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ ITS MIRROR DIRECTION, so the split cannot be satisfied by refusing every list: a
# later item that records NO approval owes no citation and stays GREEN.
_A_LATER_BULLET_WITH_NO_APPROVAL_RECORD = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> - **Corrected the §4.5 wording**, which named a form the owner rejected.")
_APPROVAL_RECORD_IN_SINGLE_ASTERISK_EMPHASIS = (
    "> *Owner approval of the exact changes* — given in-session, no comment cited.")
_APPROVAL_RECORD_IN_SINGLE_UNDERSCORE_EMPHASIS = (
    "> _Owner approval of the exact changes_ — given in-session, no comment cited.")
# ⛔ TWO RECORDS IN ONE BLOCK: the arm checks EVERY labelled paragraph, not only the
# first. Nothing landed has two (the two v3.33 entries are separate blocks whose separate
# headers both read v3.33), so the direction is pinned by fixture — measured, `records[:1]`
# left the uncited second record GREEN.
_TWO_RECORDS_ONE_UNCITED = (
    "> **Owner approval of the exact changes:** comment `6993778525`.\n"
    ">\n"
    "> **Owner approval of the follow-up** — given in-session, no comment cited.")
_TWO_RECORDS_BOTH_CITED = (
    "> **Owner approval of the exact changes:** comment `6993778525`.\n"
    ">\n"
    "> **Owner approval of the follow-up:** comment `6993778526`.")
# ⛔ THE MIRROR DIRECTION FOR THE POINTER'S REVERSED FORM (review round 2): these are
# TRUE sentences in which `issue` precedes `own` and nothing is a pointer. The unbounded
# reversal refused all four, so the reversed alternative demands the `of … own` idiom.
_NOT_A_POINTER_ON_ITS_OWN_MERITS = (
    "> Each issue is judged on its own merits.")
_NOT_A_POINTER_WITH_ITS_OWN_SECTION = (
    "> The issue has its own section above.")
_NOT_A_POINTER_WITH_ITS_OWN_SCOPE = (
    "> This is a separate issue with its own scope.")
# ⛔ AND THE IDIOM FAMILY (review round 3, all five measured REFUSED by the ``of…own``
# binding): these carry the reversed shape and name no home, so the stop-list above exists.
_NOT_A_POINTER_OF_OUR_OWN_MAKING = (
    "> That was an issue of our own making.")
_NOT_A_POINTER_OF_THEIR_OWN_DESIGN = (
    "> That problem was an issue of their own design.")
_NOT_A_POINTER_OF_ITS_OWN_ACCORD = (
    "> The issue of its own accord resolved.")
# ⛔ THE CONTAINERS A READER DOES NOT SEE (review round 3, both measured FAIL-OPEN): a
# heading, or a task-list checkbox, before a bold label is still a labelled paragraph.
_APPROVAL_RECORD_BEHIND_A_HEADING = (
    "> ### **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_BEHIND_A_TASK_LIST_CHECKBOX = (
    "> - [ ] **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ THE PLURAL POSSESSIVE (review round 3): `Owners' approval` renders as the same label
# and was ACCEPTED by a start pattern that covered `owners` but not `owners'`.
_APPROVAL_RECORD_WITH_A_PLURAL_POSSESSIVE = (
    "> **Owners' approval of the exact changes** — given in-session, no comment cited.")
# ⛔ THE SINGULAR POSSESSIVE (review round 4): `**Owner's approval …**` renders as the
# label and nothing drove it — measured, narrowing `owners?'?s?` to `owners?'?` left the
# suite GREEN while this shape was ACCEPTED.
_APPROVAL_RECORD_WITH_A_SINGULAR_POSSESSIVE = (
    "> **Owner's approval of the exact changes** — given in-session, no comment cited.")
# ⛔ AND ITS TYPOGRAPHIC TWIN (review round 4): a smart apostrophe (U+2019) renders as the
# same label and was ACCEPTED by an ASCII-only class — the arm's premise is that the
# RENDERED form is what is read, so this is the same record with a different codepoint.
_APPROVAL_RECORD_WITH_A_TYPOGRAPHIC_APOSTROPHE = (
    "> **Owner\u2019s approval of the exact changes** — given in-session, no comment cited.")
# ⛔ THE FAIL-CLOSED COST, DRIVEN AS A DIRECTION (review round 4): a bolded denial that
# OPENS the paragraph, and a bolded TOPICAL mention, are both read as records and refused.
# The arm's docstring names them as costs; these fixtures make the naming measurable.
_APPROVAL_RECORD_AS_A_BOLD_DENIAL = (
    "> **Owner approval was not required.** The change is a wording fix.")
# ⛔ THE NAMED FAIL-CLOSED COST OF THE SHARED SENTENCE SPLITTER (review round 6): an
# initial before the citation cuts the label sentence, so a CITED record is refused. The
# cost is named in the arm's residual list and driven here, so it is measured.
_APPROVAL_RECORD_CUT_BY_AN_INITIAL = (
    "> **Owner approval of the exact changes:** the owner (D. Ospina) ruled in-session; "
    "the ruling is relayed as comment `6993778525`.")
_TOPICAL_APPROVAL_MENTION_AS_A_BOLD_LABEL = (
    "> **Approval of spend** requires two owners and is unrelated to any changelog record.")
# ⛔ THE NESTED-QUOTE DIRECTIONS (review round 4): the per-record loop reads a quoted older
# approval too, so a quote that carries its own id passes and one that does not is refused.
# The test docstring promised a multiplicity assert that this cut removed; these pin what
# the code actually does.
_NESTED_QUOTED_APPROVAL_WITH_ITS_OWN_ID = (
    "> **Changelog v3.39 (2026-11-19 — x):**\n"
    ">\n"
    "> > **Owner approval of the exact changes:** comment `6993778525`.")
_NESTED_QUOTED_APPROVAL_WITHOUT_AN_ID = (
    "> **Changelog v3.39 (2026-11-19 — x):**\n"
    ">\n"
    "> > **Owner approval of the exact changes** — given in-session.")
# ⛔ THE HTML LABEL IS A DECLARED FAIL-OPEN (#13669 cycle 5), and the declaration is
# DRIVEN here: an HTML-bold label renders as the label but is not an emphasis RUN to this
# reader, so it is ACCEPTED. Closing it silently would make the residual list over-claim.
_HTML_LABEL_IS_NOT_A_RECORD = (
    "> <b>Owner approval of the exact changes</b> — given in-session.")
# ⛔ A LABEL SPLIT ACROSS TWO ADJACENT EMPHASIS RUNS IS A DECLARED FAIL-OPEN (review round
# 8): it renders as one bold label to a reader but is not ONE run to this reader, so it is
# ACCEPTED. Driven so the disclosure is measured rather than claimed.
_APPROVAL_LABEL_SPLIT_ACROSS_RUNS = (
    "> **Owner** **approval of the exact changes** — given in-session, no comment cited.")
# ⛔ AN ENTITY-ENCODED SPACE IN THE LABEL IS THE FIFTH DECLARED FAIL-OPEN (review round 9):
# `&#32;` / `&nbsp;` render as a space, `_reader_text` does not decode entities, so the
# label is not read. Naming it, plus a statement that the list is what was MEASURED rather
# than an enumeration of every rendering, is what closes the "a shape you did not think of"
# class this file's review rounds kept re-opening.
_ENTITY_ENCODED_LABEL_IS_NOT_A_RECORD = (
    "> **Owner&#32;approval of the exact changes** — given in-session, no comment cited.")
# ⛔ A LEADING DETERMINER AND A LEAD-IN WERE DECLARED FAIL-OPEN SHAPES, AND BOTH ARE
# CLOSED HERE (#13669, measured at this head rather than read off the residual list).
# `_APPROVAL_LABEL_START_RE` was anchored on the phrase itself, so `**The owner's
# approval …**` — and `**A owner approval …**` — did not render as a label to this reader
# and PASSED although they OPEN the paragraph in bold: the same record, one determiner
# away. The second shape is the one no round had named: a lead-in INSIDE the run
# (`**R1 — Owner's approval …**`, `**— Owner's approval …**`), which a release note
# writing a numbered record reaches by typing a prefix.  ⛔ MEASURED, before → after, with
# this file's own functions: both shapes ACCEPTED → REFUSED, and their CITED mirrors stay
# ACCEPTED (a widening that refused a record naming its home would be the false block the
# arm exists to avoid); over all **43** landed blocks the refusal set is IDENTICAL — the
# same **2** (v3.35, v3.33) before and after — and the landed v3.32 / v3.37 mentions and
# the plain ``no approval`` control stay GREEN.  The fail-closed cost this buys is stated
# and driven too: a TOPICAL mention carrying a determiner (`**The approval of spend** …`)
# is refused, exactly as its article-free sibling already was — the class is now
# determiner-consistent rather than one word deep.  ⛔ RESIDUAL, declared: a lead-in this
# pattern does not spell (`**v3.41 — Owner's approval …**`, `**1. Owner approval …**`) is
# still fail-OPEN until a fixture says otherwise.
_APPROVAL_RECORD_WITH_A_LEADING_DETERMINER = (
    "> **The owner's approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_WITH_AN_INDEFINITE_DETERMINER = (
    "> **A owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_WITH_A_RELEASE_ID_LEAD_IN = (
    "> **R1 — Owner's approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_WITH_A_LEADING_DASH = (
    "> **— Owner's approval of the exact changes** — given in-session, no comment cited.")
# …and the MIRRORS, so the widening is not satisfied by refusing every run that starts
# with a dash or a determiner: a record that names its home stays GREEN.
_APPROVAL_RECORD_WITH_A_LEADING_DETERMINER_AND_AN_ID = (
    "> **The owner's approval of the exact changes** — given in-session "
    "(comment `6993778525`).")
_APPROVAL_RECORD_WITH_A_RELEASE_ID_LEAD_IN_AND_AN_ID = (
    "> **R1 — Owner's approval of the exact changes** — given in-session "
    "(comment `6993778525`).")
# ⛔ THE COST THE WIDENING BUYS, DRIVEN SO IT IS A MEASUREMENT AND NOT A SENTENCE: the
# topical mention this arm already refuses without an article is refused WITH one, because
# a determiner must not be the one word that walks a shape out of scope.
_TOPICAL_APPROVAL_MENTION_WITH_AN_ARTICLE = (
    "> **The approval of spend** requires two owners and is unrelated to any changelog "
    "record.")
# ⛔ AND THE BOUND OF THE LEAD-IN RULE, DRIVEN (residual, fail-OPEN): only a short
# alphanumeric id or a bare dash is read as a lead-in, so a lead-in the pattern does not
# spell — a version, an ordinal — is still not a label to this reader and PASSES. Measured
# ACCEPTED here so the residual list's bullet is a measurement: widening the class to `.*`
# before the separator reds this fixture, which is what makes the bound a decision.
_APPROVAL_RECORD_WITH_AN_UNREAD_LEAD_IN = (
    "> **v3.41 — Owner's approval of the exact changes** — given in-session, no comment "
    "cited.")
# ⛔ THE POINTER'S FORWARD FORM NAMING A CONTAINER (#13669 cycle 5): ``its own issue
# tracker`` is a container, not a home, and is REFUSED — the stop-list binds only the
# reversed form, so this cost is driven as a fixture rather than left to be discovered.
_POINTER_NAMING_A_CONTAINER = (
    "> Each extractor keeps its own issue tracker.")
# ⛔ THE MERGED READER'S NEW FALSE BLOCK (#13669 cycle 7, measured): the shared reader now
# splits at a list item, so a colon-led pointer whose number sits in the NEXT item is
# REFUSED — while `main`'s fused read ACCEPTED it. Named in the pointer docstring and
# driven here so the cost is measured rather than discovered.
_POINTER_RESOLVED_IN_THE_NEXT_LIST_ITEM = (
    "> Two of the defects this entry records are tracked as issues of their own:\n"
    "> - #13450 — the stale sentence")
# ⛔ THE MERGED SPLIT'S OWN ALTERNATIVES, DRIVEN (review round 7): dropping `#{1,6}` or a
# bullet alternative from `_PARAGRAPH_START_RE` alone left the whole suite GREEN, so a
# later item written as `* `, `+ ` or under a heading went unread while the harness looked
# like it covered the case (its mutants rewrite BOTH regexes, so the red came from the
# strip). Each shape is a fixture so the split is pinned on its own.
_APPROVAL_RECORD_AS_A_LATER_ASTERISK_BULLET = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> * **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_AS_A_LATER_PLUS_BULLET = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> + **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_BEHIND_A_HEADING_AFTER_AN_ITEM = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> ### **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ AND A DEEPER HEADING (review round 8): `#{1,6}` is the declared class and only `###`
# was driven — narrowing both heading classes to `#{1,3}` left the whole suite GREEN.
_APPROVAL_RECORD_BEHIND_A_H4_HEADING_AFTER_AN_ITEM = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> #### **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ AND THE OTHER ENDS OF THE DECLARED CONTAINERS (review round 9): `1)` as a LATER item
# (the first-item fixture never exercised the split), `# ` as a later item, and `[X]`
# above. Each was GREEN under a narrowing of its own class while the suite stayed green.
_APPROVAL_RECORD_AS_A_LATER_PARENTHESISED_ITEM = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> 1) **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_BEHIND_AN_H1_HEADING_AFTER_AN_ITEM = (
    "> - Retired the legacy predicate in §4.5.\n"
    "> # **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ AND THE SHAPE NO FIXTURE REACHED (review: measured FAIL-OPEN, a regression from
# `main`): the heading on its OWN line and the label on the NEXT one — what a lane writes
# when it titles an entry. A heading is a whole block (CommonMark §4.2), so the label
# opens a new paragraph and is a record; before `_HEADING_START_RE` closed the heading's
# paragraph the two lines fused, the paragraph began with `###`, and the record PASSED.
_APPROVAL_RECORD_AFTER_A_HEADING_LINE = (
    "> ### v3.41 — a wording fix\n"
    "> **Owner approval of the exact changes** — given in-session, no comment cited.")
# ⛔ AND THE H4–H6 HALF OF THE CLASS, driven separately (review: narrowing the new pattern
# to `#{1,3}` left the suite GREEN while restoring the very fail-OPEN above for a `####`
# title — measured). This is the same defect the sibling regex's round-8 review caught, so
# it is closed the same way: a fixture per half of the declared class.
_APPROVAL_RECORD_AFTER_AN_H4_HEADING_LINE = (
    "> #### v3.41 — a wording fix\n"
    "> **Owner approval of the exact changes** — given in-session, no comment cited.")
# …and the SPACE is part of the class: `#NotAHeading` is an ordinary paragraph line to
# CommonMark, so it does not end anything and the next line continues it — the label does
# not open a paragraph. Dropping `\s+` to `\s*` splits here and false-blocks the shape.
_NOT_A_HEADING_WITHOUT_A_SPACE = (
    "> ####v3.41 — a wording fix\n"
    "> **Owner approval of the exact changes** — given in-session, no comment cited.")
# …and the MIRROR, so closing the heading's paragraph is not satisfied by refusing every
# line after a heading: prose below a heading records nothing and must stay green.
_NO_APPROVAL_RECORD_AFTER_A_HEADING_LINE = (
    "> ### v3.41 — a wording fix\n"
    "> The §4.5 sentence was corrected; no approval is recorded here.")
# ⛔ AND A TABLE ROW, which is ONE OF THE BLOCK KINDS `_PARAGRAPH_START_RE` does not know
# (review: measured PASS where `main` refused). Declared in the residual list; pinned green
# so the gap is measured. `docs/ONTOLOGY-CHANGELOG.md` writes no tables.
_APPROVAL_RECORD_IN_A_TABLE_ROW = (
    "> | **Owner approval of the exact changes** | given in-session, no comment cited |")
# ⛔ "ONE OF", NOT "THE ONE" (review P3): a THEMATIC BREAK and a FENCED BLOCK are two more
# block kinds this paragraph model does not name, and the label after either DOES open a
# paragraph — measured against `markdown_it(commonmark)`: `> ---` renders
# `hr, paragraph_open`, a fence renders `fence, paragraph_open`, and the row renders
# `paragraph_open`. The completeness word sent a fixer at the row's remedy, which does not
# reach them. ⛔ BUT ALL THREE ARE NOT THE SAME FAIL-OPEN READING (review P2 at
# `0e9f920d7f6`, measured): `paragraph_open` is present in every one of them, and that is
# the only property they share — the break and the fence make the label OPEN its own
# paragraph (the shape this arm reads everywhere else), while the ROW renders ONE
# paragraph whose text OPENS with the table's `|` and carries the label MID-paragraph, so
# `_opening_approval_label` returns None for it. Stated as "the label after either DOES
# open a paragraph … the same fail-OPEN as the row", the sentence was true of the break
# and the fence and FALSE of the row, and it is the row's own half of it that sends a
# reader at a property the row does not have.
# ⛔ AND THE REMEDY IS NOT THE ROW'S, MEASURED: the row closes through the STRIPPER
# (`_LEADING_LIST_MARKER_RE` taught `|\s+` → REFUSED), while these two close only through
# the SPLITTER — an END-of-paragraph boundary in `_quoted_paragraphs` (the
# `_HEADING_START_RE` shape, because a break is a whole block, CommonMark §4.1). Teaching
# `_PARAGRAPH_START_RE` their STARTS alone moves nothing (measured: both still PASS — that
# pattern is consulted on the NEXT line, which is the label), and no stripper can reach
# them either: there is no delimiter in front of the run to strip. `docs/ONTOLOGY-CHANGELOG.md`
# writes neither shape.
_APPROVAL_RECORD_AFTER_A_THEMATIC_BREAK = (
    "> ---\n"
    "> **Owner approval of the exact changes** — given in-session, no comment cited.")
_APPROVAL_RECORD_AFTER_A_FENCED_BLOCK = (
    "> ```\n"
    "> x\n"
    "> ```\n"
    "> **Owner approval of the exact changes** — given in-session, no comment cited.")


# ⛔ THE BLOCK-LEVEL MARKERS THAT START A PARAGRAPH (review round 5):
# `_LEADING_LIST_MARKER_RE` below strips ONE such marker off a paragraph's first bytes;
# this one decides where a paragraph BEGINS, which is the half that was missing — its
# absence is why an approval in a list's SECOND item was unread. A task-list checkbox and
# a heading are the same case, per CommonMark §5.2 (a list item's text is its own block).
_PARAGRAPH_START_RE = re.compile(
    r"^(?:(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?|#{1,6}\s+)")
# ⛔ AND A HEADING IS ALSO A PARAGRAPH's END (review: measured FAIL-OPEN, and a
# regression from `main`). `_PARAGRAPH_START_RE` decides where a paragraph BEGINS, so a
# heading line started one — but nothing ended it, and the NEXT line was appended to the
# heading's paragraph. Per CommonMark §4.2 an ATX heading is a whole block (its content
# is the line it is written on), so the line after it opens a NEW paragraph — which by
# this arm's own contract means its opening emphasis run is a label. Measured before this
# cut: "### v3.41 — a wording fix" followed by the label on the next line PASSED, while
# `main` refused it. ⛔ WHY NOTHING RED, stated correctly: the corpus DOES write ATX
# headings inside its blocks (measured: 11 lines match this pattern, 10 of them inside the
# changelog blocks), and they are silent only because every one of them is followed by a
# BLANK line (measured: an empty line in each of the 10 cases; `_quoted_paragraphs` treats
# both `""` and `">"` as a break), so the fused shape never arises there. `_APPROVAL_RECORD_AFTER_A_HEADING_
# LINE` pins the end-of-paragraph rule, `_APPROVAL_RECORD_AFTER_AN_H4_HEADING_LINE` drives
# the H4-H6 half of the class, and `_NOT_A_HEADING_WITHOUT_A_SPACE` pins the required space.
# ⛔ EVERY ``CommonMark §N`` IN THIS MODULE IS VERIFIED, NOT RECALLED (2026-11-19).
# Measured against the RENDERED spec at spec.commonmark.org/0.31.2 — the raw
# `spec.txt` carries no section numbers, so a citation checked against it is
# uncheckable: a THEMATIC BREAK is **§4.1**, an ATX HEADING **§4.2**, SETEXT
# HEADINGS §4.3, INDENTED CODE §4.4, a FENCED BLOCK §4.5, PARAGRAPHS §4.8 and
# LIST ITEMS §5.2. An earlier cut of these comments cited **§4.4 for a break**
# and **§4.3 for an ATX heading** — the two adjacent-section errors a reader
# cannot catch without the rendered spec, which is why the version is stamped
# here rather than left for the next lane to re-derive. Both claims the numbers
# annotate are separately measured by the fixtures below; only the numbers moved.
_HEADING_START_RE = re.compile(r"^#{1,6}\s+")


def _quoted_paragraphs(block: str) -> list[str]:
    """The block's quote PARAGRAPHS, quote prefixes stripped and wrapping joined.

    A PARAGRAPH, not a line: the approval record WRAPS (its citation sits on a later
    physical line than the bold label), so a line-scoped read would refuse a record
    that does name its home — a false block on a true sentence.

    ⛔ AND A LIST ITEM IS ITS OWN PARAGRAPH (review round 5, measured FAIL-OPEN and a
    regression from `main`): a blank line separates blocks, but a COMPACT list separates
    nothing — consecutive bullets sit on consecutive lines — so joining by line fused
    `- did A` / `- **Owner approval …**` into one paragraph, and only the FIRST item's
    opening run could ever be read. CommonMark §5.2/§4.8 settle what a paragraph is: each
    list item is a container block whose text is its OWN paragraph, so each item's first
    inline run does open one — the exact contract this arm is built on. A heading splits
    the same way, for the same reason: it STARTS a paragraph, and — because a heading is a
    whole block, CommonMark §4.2 — it also ENDS one, so the line after it begins a new
    paragraph rather than continuing the heading.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in block.splitlines():
        if line.strip() in {"", ">"}:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        text = re.sub(r"^[\s>]+", "", line)
        if current and _PARAGRAPH_START_RE.match(text):
            paragraphs.append(" ".join(current))
            current = []
        current.append(text)
        if _HEADING_START_RE.match(text):
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


_SENTENCE_END_RE = re.compile(r"[.!?][\"'*_`)\]]*(?=\s)")
# ⛔ `;` IS NOT A TERMINATOR, MEASURED (review @ 95a8df2f): a reader does not see a
# sentence break at a semicolon, and treating one as a break refused TRUE prose —
# ``… its own issue; see #13450 for it.`` was three pseudo-sentences to this file and
# one sentence to its reader, so the id landed in the wrong half and the pointer was
# refused. Dropping `;` WIDENS the sentence, which is the fail-OPEN direction for a
# borrowed id — so the widening is stated as a residual in both arms' docstrings above,
# and `_POINTER_RESOLVED_AFTER_A_SEMICOLON` pins the direction that matters (resolved
# prose must stay green).
# ⛔ …AND THE ABBREVIATIONS ARE MASKED, not split on: `e.g.`/`i.e.` end in a dot that is
# no terminator, and splitting there refused a label sentence whose citation followed an
# `e.g.` (measured). Only these two are masked — ``etc.`` is left alone because it DOES
# end sentences, and masking it would merge two of them.
_ABBREVIATION_RE = re.compile(r"\b(?:e\.g|i\.e)\.", re.IGNORECASE)
_DOT_SENTINEL = "\u2024"


def _sentences(text: str) -> list[str]:
    """`text` split at sentence boundaries, a closing quote/emphasis run included.

    ⛔ THE CLOSING RUN IS WHY THIS IS NOT ``re.split(r"(?<=[.;!?])\\s+")``: this
    document closes a sentence with a QUOTATION and then its emphasis
    (``… is a stale sentence."* Citing the comment …``), so a splitter keyed on the
    character immediately before the space sees ONE sentence where a reader sees two.
    The scope below is the reader's two, not the splitter's one — measured: with the
    naive splitter the landed v3.38 record is a single sentence and the borrowed id in
    the second half passes as if it were the approval's.
    """
    out: list[str] = []
    guarded = _ABBREVIATION_RE.sub(
        lambda match: match.group(0).replace(".", _DOT_SENTINEL), text)
    last = 0
    for match in _SENTENCE_END_RE.finditer(guarded):
        out.append(guarded[last:match.end()])
        last = match.end()
    out.append(guarded[last:])
    return [sentence.replace(_DOT_SENTINEL, ".")
            for sentence in out if sentence.strip()]


def _opening_approval_label(paragraph: str) -> str | None:
    """The bold label that OPENS `paragraph`, when it is an owner-approval label.

    The corpus states its own structure: every bolded approval label in
    `docs/ONTOLOGY-CHANGELOG.md` opens the paragraph it labels, a denial never does, and
    no incidental mention of approval OPENS *with the phrase* (three paragraph-opening
    bold runs mention approval without being labels — v3.37 ``Corrected in review, after
    the approval was given…``, v3.32's build record, v3.31's rulings — and none of them
    begins with the label, which is what the code reads). Reading THAT is what makes the
    detector structural rather than another phrasing (#13669); the rendered form inside
    the run is what is matched, so a doubled space, emphasis on the label's last word,
    `__…__` and TITLE/ALL-CAPS casing all read as the same label.

    ⛔ IT RETURNS THE RENDERED RUN, not a boolean, so a caller (and a failure message)
    can show the lane what it read; `None` means "this paragraph records no approval",
    which is the direction that must stay GREEN.
    """
    match = _OPENING_EMPHASIS_RE.match(
        _LEADING_LIST_MARKER_RE.sub("", paragraph.strip()))
    if not match:
        return None
    run = _reader_text(match.group(2))
    return run if _APPROVAL_LABEL_START_RE.match(run) else None


def _refuse_an_approval_record_with_no_resolvable_home(block: str) -> None:
    """A release that RECORDS an owner approval must say where that approval lives.

    ⛔ THIS IS NOT THE UNCONDITIONAL DEMAND the pin above measured to false-block. That
    demand was "the newest block carries a comment id", which reds a legitimate
    release that changes something else and cites nothing; the file's practice does
    not support it. This arm speaks ONLY when the block RECORDS an owner approval — a
    release that never mentions one owes no citation and stays green.

    The file's own record is why the arm exists at all: its CRITICAL RULE requires
    Daniel Ospina's explicit approval of the exact changes, and its v3.37 entry
    records that *"the artifact is the text itself"* is the form the owner REJECTED,
    because a reader cannot resolve it. An approval record with no resolvable home is
    a release asserting a decision it cannot point at.

    ⛔ THE CITATION MUST BE IN THE SENTENCE THAT RECORDS THE APPROVAL, not merely in
    the paragraph (measured under review). A paragraph-wide search is satisfied by ANY
    id the record happens to mention, so deleting the approval's OWN relay left this
    arm GREEN on the landed v3.38 entry: the record could still point only at the
    comment about the form the owner rejected (``the form the owner rejected for v3.36
    (comment `6965147045`)``), which is precisely the state this arm exists to refuse.
    An id in a LATER sentence belongs to whatever that sentence says.
    ⛔ THE SCOPE IS THE SENTENCE, NOT THE ATTRIBUTED-QUOTATION FORM, and the two were
    measured against the landed text before choosing. The attributed ``comment <id>:
    "…"`` form the §4.3 pin demands would refuse FOUR of the six accepted records (only
    **2** carry it — v3.38 and v3.36, measured; #13669 corrected this comment's earlier
    "none"); the sentence scope accepts a bare ``comment <id>`` and still refuses the
    borrow.
    ⛔ WHAT A RECORD *IS* WAS RE-DECIDED IN #13669, because the phrase detector this arm
    shipped with was measurably vacuous. The shipped detector keyed on ``owner approval``
    followed by a COLON inside one sentence. Measured at the merged head ``6d761643``
    (the squash of #13643, re-measured at ``4531e7e2``):
    ``**Owner approval of the exact changes** — given in-session … The artifact is the
    text itself.`` was **ACCEPTED** (the bold label carries no colon), and so was the
    landed v3.32 record with its comment id deleted — the arm slept through the exact
    artifact it exists to refuse. Requiring the colon had also bought a claim the code
    did not perform: ``No owner approval was required: …`` was **REFUSED**, so the
    file's "no approval" control passed on the colon requirement alone — a colon was
    absent from its sentence — and not on structure. Rounds 1-5 of that PR each closed
    one phrasing and exposed its complement, because prose has no finite phrasing set.
    ⛔ SO THE DETECTOR READS THE CORPUS'S OWN STRUCTURE: an emphasis run that OPENS a
    paragraph, whose rendered text BEGINS with ``Owner approval`` / ``Approval``. All
    **8** bolded approval labels in the landed corpus open their paragraph with the
    phrase, so this is the document's convention, not a heuristic about it. A negation
    IN PROSE is excluded BY CONSTRUCTION — ``No **owner approval** was required:`` does
    not OPEN with the phrase — instead of by a word list, which is what makes that
    control green without the colon that used to do the work by accident. ⛔ A BOLDED
    denial that DOES open the paragraph is the opposite case and is refused; that cost,
    and a bolded TOPICAL mention (``**Approval of spend** requires two owners``), are
    named in the residual list below rather than glossed here.
    ⛔ THE NUMBERS THIS DOCSTRING PROMISES ARE RE-DERIVED BY THE TEST BELOW (#13669's
    acceptance criterion), not asserted here alone. Measured at this head over all 43
    landed blocks: **8** open a paragraph with a bold approval label, **6** of those
    carry a comment id in that label's sentence, and **2** LANDED entries are refused —
    **v3.35** (2026-11-13, cites nothing at all) and **v3.33** (2026-11-11,
    ``**Approval** is the owner's decision D2 — A on #11824``, which points at an issue
    and a decision label but at no words a reader can check). Both are FALSE BLOCKS on
    true prose, stated rather than glossed: the test reads the NEWEST block alone, which
    passes, and a lane sweeping the corpus should expect these two.
    ⛔ THE RESIDUALS OF THE STRUCTURAL CONTRACT — the shapes MEASURED at this head, NOT an
    enumeration of every way a label can render. Nine are fail-OPEN, which is the direction
    that matters: an unread record, an unread HTML label, a label split across runs, a label
    behind a table row, a label behind a thematic break, a label behind a fenced block, an
    entity-encoded space, a lead-in this arm's bounded rule does not spell and an id borrowed
    from a contrastive parenthetical all PASS, so this arm reduces the
    vacuity #13669 measured rather than ending it, and a reading this list does not name
    should be treated as fail-OPEN until a fixture says otherwise:
      - an UNBOLDED, or MID-PARAGRAPH, record is OUT OF SCOPE and therefore **PASSES**:
        ``Owner approval: comment `NNN`.`` in plain prose, and ``The change landed.
        **Owner approval …**``, are not read as records — emphasis is the corpus's only
        structural signal for a label, the phrase detector is what #13669 measured
        vacuous, and reading a mid-paragraph bold run is what makes the bolded-denial
        control above a false block. ⛔ A SECOND (or later) LIST ITEM is NOT this
        residual: each item is its own paragraph (CommonMark §5.2), so its opening run is
        read like any other — ``_quoted_paragraphs`` splits at the item boundary, and
        ``_APPROVAL_RECORD_AS_A_LATER_*`` pin it because the fused-paragraph read PASSED
        a later-item record while `main` refused it;
      - a BOLDED DENIAL that OPENS a paragraph (``**Owner approval was not required.**``)
        is read as a record and refused, and so is a bolded TOPICAL mention
        (``**Approval of spend** requires two owners``) — the cost of excluding negations
        structurally (fail-closed);
      - a quotation of an older approval inside a nested ``> > `` run is read as a second
        record and its citation is demanded (fail-closed);
      - an HTML ``<b>`` / ``<strong>`` label (``<b>Owner approval …</b>``) is not an
        emphasis RUN to this reader and is therefore **not read as a record** (fail-open,
        out of scope: the corpus writes ``**`` exclusively — no ``<b>`` or ``<strong>``
        appears in the file);
      - a label NOT CONTAINED IN A SINGLE EMPHASIS RUN is therefore **PASSES** (fail-open,
        out of scope): ``**Owner** **approval of the exact changes**`` splits the phrase
        across two runs, so no single run renders the label and
        ``_opening_approval_label`` returns ``None``; driven by
        ``_APPROVAL_LABEL_SPLIT_ACROSS_RUNS`` so the shape is measured. ⛔ THE OTHER TWO
        SHAPES A READER RENDERS THE SAME WAY DO NOT FAIL FOR THAT REASON, and the
        difference is stated because it decides which fix would close them:
        ``**Owner <b>approval</b> …**`` and ``**[Owner approval …](url)**`` ARE one run —
        it is ``_reader_text`` that strips no HTML tags and no link syntax, so the run's
        rendered text carries ``<b>``/``</b>`` or ``[...](url)`` and does not begin with
        the phrase. Closing either needs the MARKUP stripped first, not the run unsplit;
      - a label behind one of the THREE BLOCK KINDS this paragraph model does not name — the
        table ROW (``| **Owner approval …** | given in-session |``), a THEMATIC BREAK
        (``> ---``, then the label) or a FENCED BLOCK (a ``` fence, then the label) —
        **PASSES** (fail-open, out of scope). ⛔ THEY ARE ONE CLASS WITH TWO DIFFERENT FIXES,
        which is why they are named separately instead of as "the one block kind" (review
        P3: the completeness word pointed the next fix at the wrong half). Every one is
        measured against ``markdown_it(commonmark)``, and the READING is what splits the
        class in two (⛔ NOT "all three give the label its own paragraph" — that reads as
        if every paragraph OPENS with the label, which is true only of the break and the
        fence): the BREAK and the FENCE make the label OPEN its own paragraph, while the
        ROW makes ONE paragraph whose text OPENS with the table's leading ``|`` and carries
        the label mid-paragraph — the same reason a mid-sentence bold run passes. What
        differs is what has to learn about it:
        · the ROW's opening run is not read because the table's leading ``|`` sits BEFORE it
          — the same reason a mid-sentence bold run passes — so the fix lives in the
          STRIPPER, not in ``_PARAGRAPH_START_RE``: teaching that pattern a `|` row start
          changes nothing (0 of the fixtures move), while ``_LEADING_LIST_MARKER_RE``
          taught `|\\s+` consumes the cell delimiter and closes the shape
          (``_APPROVAL_RECORD_IN_A_TABLE_ROW`` PASS → REFUSED, measured). The bare-`|` form
          that consumes no space changes nothing either, and is not a row start. Measured
          PASS where `main` refused; the corpus writes no tables
          (``grep -c '^> *|' docs/ONTOLOGY-CHANGELOG.md`` = 0);
        · the BREAK and the FENCE close through the SPLITTER only, and no stripper can reach
          them — there is no delimiter in front of the run, the block IS the delimiter.
          They need an END-of-paragraph boundary in ``_quoted_paragraphs`` (the
          ``_HEADING_START_RE`` shape, a break being a whole block, CommonMark §4.1).
          Measured, and it is why the guess costs a cycle: adding their STARTS to
          ``_PARAGRAPH_START_RE`` alone leaves BOTH still PASS (that pattern is consulted on
          the NEXT line, which is the label), while an end-boundary split turns both
          PASS → REFUSED and moves the row not at all. The corpus writes neither shape
          (``grep -c '^> *\\(---\\|```\\)' docs/ONTOLOGY-CHANGELOG.md`` = 0);
        and all three are driven green — by ``_APPROVAL_RECORD_IN_A_TABLE_ROW``,
        ``_APPROVAL_RECORD_AFTER_A_THEMATIC_BREAK`` and
        ``_APPROVAL_RECORD_AFTER_A_FENCED_BLOCK`` — so removing one, or handling it, is a
        visible decision rather than a silent one;
      - an ENTITY-ENCODED space (``**Owner&#32;approval …**``, ``**Owner&nbsp;approval
        …**``) renders as the label but ``_reader_text`` decodes no entities, so it
        **PASSES** (fail-OPEN); driven by ``_ENTITY_ENCODED_LABEL_IS_NOT_A_RECORD``;
      - an id anywhere in the label's SENTENCE still satisfies it and therefore **PASSES**,
        so a contrastive parenthetical naming another id (v3.37's shape) is accepted, and
        telling WHICH of two ids records the approval needs the comment's own text — the
        reviewer step recorded in #12068;
      - a LEAD-IN the bounded rule does not spell (``**v3.41 — Owner's approval …**``,
        ``**1. Owner approval …**``) is not read as a label and **PASSES** (fail-OPEN): the
        lead-in class is a short alphanumeric id or a bare dash, deliberately narrow so it
        cannot swallow prose, and widening it towards ``.*`` is what makes it read — driven
        by ``_APPROVAL_RECORD_WITH_AN_UNREAD_LEAD_IN``;
      - a label sentence whose first sentence is cut by an initial or by ``etc.`` before
        the citation (``… the owner (D. Ospina) ruled … comment `NNN```) is REFUSED —
        fail-closed, and it is the SHARED reader's split that cuts it
        (``_ABBREVIATION_RE`` masks only ``e.g.``/``i.e.``); driven as a fixture below.
    """
    # ⛔ THE RECORD IS READ FROM THE PARAGRAPH'S OPENING EMPHASIS RUN, on the rendered
    # text (review @ e4927f5f, @ 95a8df2f, then #13669): `**Owner approval` as a literal
    # missed `**Owner  approval` (a doubled space), `**Owner *approval*` / `__Owner
    # approval …__` (markup that renders as the same label), `**Owner Approval**` /
    # `**OWNER APPROVAL**` (another casing), and — the vacuous half — every label with no
    # colon, which is most of the corpus. `_reader_text` drops the markup and collapses
    # the spacing inside the run, and `_APPROVAL_LABEL_START_RE` reads case-insensitively,
    # so the RENDERED label is what is read. ⛔ THE CITATION is still read from the RAW
    # sentence, so markup can neither hide nor manufacture an id.
    records = [paragraph for paragraph in _quoted_paragraphs(block)
               if _opening_approval_label(paragraph)]
    if not records:
        # ⛔ RETURN, DO NOT ASSERT (review F1). This release records no owner approval,
        # so it owes no citation: the unconditional demand was measured to false-block
        # the 36 of the 43 landed blocks that carry no comment id (re-derived by the
        # corpus block in the test below).
        return
    # ⛔ EVERY record is checked, and the multiplicity assert the shipped cut carried is
    # GONE (#13669). That assert refused any block carrying two labelled paragraphs on a
    # message about a second copy shadowing the first. Measured: NO landed block carries
    # two today — the two v3.33 entries are separate blocks whose separate headers both
    # read v3.33, one label each — so the assert was not a live false block, and removing
    # it is pinned by the `_TWO_RECORDS_*` fixtures below rather than by a corpus count.
    for record in records:
        # ⛔ THE LABEL'S SENTENCE IS THE PARAGRAPH'S FIRST, not a `next(...)` search
        # (#13669 finding 3). Given OPENING-only detection the two are the same sentence,
        # so this cut is a simplification rather than an independently pinned fix: what
        # the full-stop fixture below measures is the FAILURE TYPE, because the shipped
        # `next(...)` had no default and raised a bare `StopIteration` — an uncaught
        # non-AssertionError with no message, which `_assert_refused_by` cannot classify.
        # ⛔ THE MARKER IS STRIPPED BEFORE THE SPLIT (review round 6, measured): `1. `
        # ends in a full stop, so `_sentences` cut the label sentence down to `1.` and a
        # CITED ordered item — `1. **Owner approval …**: comment `6993778525`.` — was
        # refused for a missing citation that was in the sentence. `- `, `- [ ] `, `### `
        # and `1) ` never had the problem; only the `.`-terminated ordered marker (`\d+\.`)
        # did — `1)` is a member of `\d+[.)]` and `_sentences` keeps it whole.
        label = _sentences(_LEADING_LIST_MARKER_RE.sub("", record.strip()))[0]
        assert _CITATION_RE.search(label), (
            f"the changelog block {NO_RESOLVABLE_HOME_DIAGNOSIS} "
            "in the SENTENCE that records it, so the approval has no home a reader "
            "can resolve — cite the comment that records it in that same sentence "
            "(``comment <id>`` or the permalink), as the v3.37 entry does. An id in a "
            "later sentence is that sentence's, not the approval's. *\"The artifact is "
            "the text itself\"* is the form the owner rejected; the paragraph reads: "
            f"{record!r}")


_LEADING_LIST_MARKER_RE = re.compile(
    r"^(?:(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?|#{1,6}\s+)")
# ⛔ THE REFUSAL'S DIAGNOSIS IS ONE LITERAL, SHARED BY THE ARM AND THE PIN (review P3):
# the arm's message and the fixture assertion that checks it is "a diagnosis a lane can
# act on" were two copies of the same phrase, so a legitimate reword red the fixture while
# `_assert_refused_by` promises (in the same file) that "no arm's message WORDING is
# involved" in attribution. Hoisting it keeps the property under test — the refusal
# carries a DIAGNOSIS — without pinning the prose that states it: a reword now moves both
# sides together, and deleting the diagnosis still reds the fixture.
NO_RESOLVABLE_HOME_DIAGNOSIS = "records an owner approval but names no comment id"
# ⛔ THE RUN MAY BE SINGLE-MARKER EMPHASIS TOO, AND MAY SIT BEHIND A LIST MARKER OR A
# HEADING (review rounds 2, 3 and 5, all measured FAIL-OPEN): `- **Owner approval …**`,
# `1. **Owner approval …**`, `- [ ] **Owner approval …**`, `### **Owner approval …**`,
# `*Owner approval …*` and `_Owner approval …_` all render as a labelled paragraph, and
# a detector anchored on `**`/`__` at the paragraph's first byte slept through every one.
# ⛔ AND THE STRIP BELOW ONLY EVER REACHED THE FIRST ITEM (review round 5): the marker is
# what remains at a paragraph's start, so `_PARAGRAPH_START_RE` (above, where the paragraph
# BEGINS) is what makes a LATER item's run readable at all; this pattern then strips the
# marker that split placed at the front.
_OPENING_EMPHASIS_RE = re.compile(r"^(\*\*\*|\*\*|__|\*|_)(.+?)\1", re.S)
# ⛔ THE PLURAL POSSESSIVE IS IN THE CLASS (review round 3): `Owners' approval` renders as
# the same label, and `owner'?s?` covered `owner` / `owner's` / `owners` but not `owners'`.
# ⛔ A DETERMINER AND A LEAD-IN ARE PART OF THE LABEL, NOT A WAY OUT OF IT (#13669):
# anchored on the phrase alone, `**The owner's approval …**` — and `**R1 — Owner's
# approval …**` — opened the paragraph in bold and PASSED unread, so the same record sat
# one typed word away from being detected. Both are read now. The lead-in is deliberately
# BOUNDED, because a wide prefix would swallow mid-sentence emphasis: a short
# alphanumeric release id (`R1`, `D2`, `ID12` — `[A-Za-z]` under IGNORECASE) or a bare
# dash, each followed by one separator. A lead-in outside that class (`v3.41 — `, `1. `)
# stays fail-OPEN and is named in the residual list above, rather than widening this until
# it catches prose.
_APPROVAL_LABEL_START_RE = re.compile(
    r"^(?:(?:[A-Z]{1,3}\d{0,3}\s*[\u2014\u2013-]\s*|[\u2014\u2013-]\s*))?"
    r"(?:(?:an?|the)\s+)?(?:owners?['\u2019]?s?\s+)?approval\b",
    re.IGNORECASE)
# ⛔ THE POINTER PHRASE IS READ IN EITHER WORD ORDER (#13669 finding 2): ``an issue of
# its own`` / ``a tracking issue of its own`` is the same pointer as ``its own issue``,
# and the shipped adjacency-exact pattern (`own` then up to three words then `issue`)
# missed both, so a defect naming a home it cannot give passed unread.
# ⛔ THE REVERSED FORM IS BOUND TO THE IDIOM, AND THE BINDING IS NOT A GUARANTEE — measured
# (review rounds 2 and 3): an unbounded ``issue … own`` window refused TRUE prose (``Each
# issue is judged on its own merits.``, ``The issue has its own section above.``), and
# ``of … own`` alone still caught the idiom family (``an issue of our own making``,
# ``of their own design``, ``of its own accord``). So the reversed form demands ``of…own``
# AND refuses the listed idiom heads. ⛔ THE RESIDUAL IS STATED: an UNLISTED idiom of the
# same shape (``an issue of its own wording``) is still refused — fail-CLOSED, remedy is
# to extend this list, which is the safer direction even though a list of English idioms
# cannot be completed.
_OWN_ISSUE_RE = re.compile(
    r"\bown\b(?:\s+\S+){0,3}\s+issues?\b"
    r"|\bissues?\b\s+(?:of|all\s+of)\s+(?:\w+\s+){0,2}own\b"
    r"(?!\s+(?:accord|behalf|design|devising|making|merits|right|sake|terms|volition)\b)",
    re.IGNORECASE)
_ISSUE_NUMBER_RE = re.compile(r"#\d{2,}")


def _reader_text(sentence: str) -> str:
    """`sentence` as a READER sees it: whitespace collapsed, emphasis markers dropped.

    ⛔ THE POINTER ARM MATCHES WHAT THE DOCUMENT RENDERS, not the bytes it was typed
    with. Measured green before this (review @ 30ef349c round 2, then @ 95a8df2f):
    ``with its own  issue`` (a doubled space), ``with its own *issue*`` (markdown
    emphasis, which renders as ``own issue``), ``its own GitHub issue``, ``its own
    issues`` and ``own Issue`` all escaped ``_OWN_ISSUE_RE``, so the one phrase this arm
    exists to refuse passed unread in the shapes a re-wrap, a copy-edit and a
    qualification actually produce. Neither spacing, nor a markup run, nor a
    qualifier between the words is a semantic boundary.

    ⛔ THE NUMBER IS READ FROM THE SAME NORMALISED TEXT, so markup can neither hide a
    pointer nor manufacture one: ``#13450`` survives the transformation.
    """
    return re.sub(r"\s+", " ", sentence.replace("*", "").replace("_", "").replace("`", "")).strip()


# ⛔ ONE DETECTOR, NOT TWO (merge reconciliation with `main`'s #13679). `main` carries a
# PARAGRAPH-WIDE bold scan (`_bolded_approval_label`) beside this file's OPENING-ONLY one;
# they are two answers to the same question, and keeping both would let a later lane read a
# report from whichever one it happened to call. This file keeps the opening-only detector,
# which is the detector this arm's fixtures, corpus pins and residuals were written against.
# ⛔ THE REASON IS THE MEASURED TRADE, NOT A CLAIM THAT THE OTHER DETECTOR CANNOT WORK —
# list-item readability comes from `_quoted_paragraphs`' item split, which BOTH detectors
# share (measured: `main`'s scan over this file's paragraph model reads a compact list's
# later item without any marker strip; the marker strip exists because the opening anchor
# needs it). Over the landed corpus and that same paragraph model the two detectors agree
# on 42 of 43 blocks, and the single disagreement is v3.33, which this arm REFUSES as a
# record with no comment id while `main` exempts it as out of scope. So the two real
# questions this merge decided are:
#   1. a label that does not OPEN its paragraph is not read here (fail-OPEN) where `main`
#      read it — declared in the residual list and driven by
#      `_APPROVAL_LABEL_AFTER_AN_UNBOLDED_MENTION` and
#      `_PREFIX_APPROVAL_RECORD_WITH_AN_UNBOLDED_MENTION_CITING_AN_ID`, so the boundary is
#      a pin rather than a sentence nobody measures;
#   2. a bare ``**Approval**`` label IS in scope here, which is what makes v3.33 a false
#      block and ``**Approval of spend**`` a false block too — both are named in the
#      residual list as the fail-closed cost of covering the shape #13669 says must be
#      refused or declared out of scope.
# Neither is a ruling this file can make on its own; both are recorded in the PR body so a
# later lane re-deciding them meets the trade instead of discovering it.


def _refuse_an_unresolvable_pointer(block: str) -> None:
    """A sentence that names an issue as something's home must give its number.

    The #5063 class this repo already chases in `tests/test_docs_citation_refs.py`: a
    pointer a reader is told to follow that resolves to nothing. *"its own issue"*,
    with no number, is that pointer — the reader lands on no issue at all, and the
    defect the sentence disclaims is tracked by nothing a reader can find.

    ⛔ UNFOLDED WITHIN A PARAGRAPH, THEN SPLIT BY THE FILE'S OWN ``_sentences``, and
    each half is load bearing — every one was measured FAIL-OPEN on its own (review @
    30ef349c, findings P2 and P3):
      - a soft wrap between ``own`` and ``issue`` put the phrase out of reach of
        ``_OWN_ISSUE_RE``, so a reflow of any entry silently disabled this arm;
      - a sentence closed by a quotation put the NEXT sentence's ``#NNNN`` inside this
        one, so a pointer borrowed its neighbour's number;
      - a doubled space or an emphasis run (``own *issue*``) escaped the phrase, in
        the two shapes a copy-edit actually produces;
      - and unfolding the WHOLE block absorbed the next PARAGRAPH's id into a pointer
        that ended without terminal punctuation — a different sentence by this arm's
        own scope, so the run is bounded per paragraph here.
    Fixtures below drive them one at a time, so a later cut that drops any fix reds
    instead of passing.

    ⛔ THE RESIDUAL, STATED: an id anywhere in the SAME sentence satisfies it, so
    ``… its own issue, unlike #13450;`` is accepted. Separating that needs the
    sentence's grammar, not its characters, and the reader-visible defect here is the
    pointer with no number at all. ⛔ AND the same widening is why `;` is no longer a
    terminator (a semicolon is not a sentence break to a reader) — stated HERE, next to
    the window it widens, rather than left to be discovered.
    ⛔ AND THE FORWARD FORM'S OWN COST IS NAMED (#13669 cycle 5): the idiom stop-list binds
    the REVERSED form only, so a forward ``own … issue`` followed by a QUALIFIER NOUN
    (``Each extractor keeps its own issue tracker``, ``their own issues list``) names a
    container rather than a home and is REFUSED — fail-CLOSED, driven by
    ``_POINTER_NAMING_A_CONTAINER`` below so the cost cannot be closed silently.
    ⛔ AND A LIST ITEM IS A NEW PARAGRAPH BOUNDARY (#13669 merge, measured): the shared
    reader now splits at every block-level marker, so a colon-led pointer whose number
    sits in the NEXT item — ``Two of the defects are tracked as issues of their own:`` /
    ``- #13450 — the stale sentence`` — is REFUSED where `main`'s fused read accepted it.
    That follows this arm's per-paragraph scope, but a READER resolves it, so it is a
    FALSE BLOCK and it is stated here rather than met as a bug; driven by
    ``_POINTER_RESOLVED_IN_THE_NEXT_LIST_ITEM``.
    ⛔ AND ONE QUOTED SHAPE IS STILL REFUSED, named because the approval arm names its
    own: text inside a nested ``> > `` run is a QUOTATION OF DEFUNCT PROSE, not a claim
    this release makes, and this arm reads it as a claim — so an entry quoting the old
    ``its own issue`` sentence to record its correction is refused. Nothing in the
    landed corpus does this today; the fix is to skip nested-quote paragraphs, and it is
    a change to the shared reader, not to this arm.
    """
    for paragraph in _quoted_paragraphs(block):
        for sentence in _sentences(paragraph):
            text = _reader_text(sentence)
            if _OWN_ISSUE_RE.search(text) and not _ISSUE_NUMBER_RE.search(text):
                raise AssertionError(
                    "a changelog sentence names an issue as the home of a defect but gives "
                    "no issue number, so a reader cannot follow it — cite the issue "
                    f"(``#NNNNN``): {sentence!r}")


def _assert_refused_by(raised: pytest.ExceptionInfo[AssertionError], arm: str) -> None:
    """The fixture was refused BY `arm`, not by a neighbour that merely raised.

    ⛔ THE DISCIPLINE THIS FILE ALREADY STATES AT ``:2840-2870``: a bare
    ``pytest.raises(AssertionError)`` reported success there while the swept arm had
    refused NOTHING — 60 passed, a surviving mutant — because the pin's own downstream
    assertion raised instead. Each arm now has ONE raise site (this cut removed the
    approval arm's second, the multiplicity assert), so a fixture that stops carrying the
    shape it names moves the catch and the pin goes on reporting green.
    The frame name is read from the traceback, so no arm's message WORDING is involved
    and a legitimate reword cannot red this test; the directions stay distinguishable
    because each fixture is shaped so that only its own assert can fire — one approval
    paragraph per fixture, so the per-record loop below has a single subject, with the
    two ``_TWO_RECORDS_*`` fixtures as the DELIBERATE exception (every-record checking is
    exactly what they pin) — and the arm has one raise site, so the frame stays
    unambiguous.
    """
    frames = {frame.name for frame in traceback.extract_tb(raised.tb)}
    assert arm in frames, (
        f"the fixture was refused, but not out of {arm} — a neighbouring assertion "
        "fired instead, so this direction measured nothing. "
        f"Frames seen: {sorted(frames)}")


def test_a_release_recording_an_owner_approval_names_where_it_lives() -> None:
    """The approval's home resolves, and a defect pointer resolves — for ANY release.

    ⛔ WHY THIS ARM EXISTS AT ALL, stated as the measured failure: the v3.38 entry
    shipped an approval record reading *"the artifact is the text itself"* and a
    pointer reading *"its own issue"* — the two corrections its own PR body lists
    among the defects review caught. Neither was in the artifact, and no arm could
    see either: the §4.3 pins above are scoped to a block that quotes that clause,
    which this release does not, so they return before reading a word of it. An arm
    scoped by CONTENT cannot guard an entry whose content is something else.

    ⛔ WHAT THIS ARM DOES NOT DECIDE, TWO NESTED-QUOTE SHAPES STATED RATHER THAN IMPLIED
    (both measured absent from the landed corpus at this head): a block that QUOTES an
    older approval inside a nested ``> > `` run is read as a record by the per-record loop
    — NOT by a multiplicity assert, which this cut removed — so a quoted approval that
    carries its own id is ACCEPTED and one that does not is REFUSED. Both directions are
    fixtures below, so the sentence is measured rather than asserted.

    ⛔ WHAT THIS ARM DOES NOT DECIDE, stated rather than implied: it cannot verify
    that the CITED approval is real (that is the reviewer step
    ``gh api repos/.../issues/comments/<id>`` performs, recorded in #12068), and it
    cannot see whether an ISSUE the prose cites is OPEN — issue state lives in the
    tracker, not in the tree, so a sentence asserting a closed issue is open is
    beyond a static suite. That is exactly how the third defect of the same release
    shipped (*"remains open (#9063)"* for an issue closed COMPLETED in 2026-10-20);
    it was corrected in the text. ⛔ **Updated**: the CLAIM is now pinned for that
    release by ``tests/test_13634_9063_pointer_resolves.py`` (claim forms; a quoted
    span is skipped only when a correction marker introduces it) — what still has no
    arm is the TRACKER STATE itself, which no static suite can read.

    ⛔ AND EACH DIRECTION IS ATTRIBUTED TO THE ARM THAT REFUSED IT (``_assert_refused_by``),
    not to "something raised": a bare ``pytest.raises`` is the exact shape this file's
    sibling sweep found reporting success over a surviving mutant.
    """
    block = _newest_block_body(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    # CONTROL RUN: the landed block must PASS both arms, so a probe that refuses for
    # an unrelated reason cannot satisfy the negative directions below.
    _refuse_an_approval_record_with_no_resolvable_home(block)
    _refuse_an_unresolvable_pointer(block)
    # CONTROL (review F1): a block that records NO approval owes no citation. Pre-fix
    # this raised — `len(records) == 1` fires on zero — so the arm refused the 36
    # landed blocks it promises to leave alone.
    _refuse_an_approval_record_with_no_resolvable_home(_NO_APPROVAL_RECORD)
    # …and again with the negative sentence carrying a SENTENCE-INITIAL capital (review @
    # 95a8df2f), and again with the approval word BOLDED mid-sentence (#13669 finding 5,
    # which the shipped detector REFUSED while its comment claimed the colon kept it out):
    # a denial does not OPEN a paragraph, so the structural read excludes it.
    _refuse_an_approval_record_with_no_resolvable_home(_NO_APPROVAL_RECORD_IN_PROSE)
    # ⛔ A LABEL AFTER A HEADING LINE IS A RECORD (review: measured FAIL-OPEN, regression
    # from `main`): the heading is its own block, so the label opens the next paragraph.
    with pytest.raises(AssertionError) as after_heading:
        _refuse_an_approval_record_with_no_resolvable_home(
            _APPROVAL_RECORD_AFTER_A_HEADING_LINE)
    _assert_refused_by(after_heading, "_refuse_an_approval_record_with_no_resolvable_home")
    # ⛔ …AND FOR EVERY DECLARED HEADING LEVEL (review: only `###` was driven, so the
    # H4–H6 half of the class could be narrowed away in silence).
    with pytest.raises(AssertionError) as after_an_h4:
        _refuse_an_approval_record_with_no_resolvable_home(
            _APPROVAL_RECORD_AFTER_AN_H4_HEADING_LINE)
    _assert_refused_by(after_an_h4, "_refuse_an_approval_record_with_no_resolvable_home")
    # …and a `####` line with NO space is not a heading, so it continues the paragraph and
    # the label below it opens nothing — green, which is what keeps the `\s+` honest.
    _refuse_an_approval_record_with_no_resolvable_home(_NOT_A_HEADING_WITHOUT_A_SPACE)
    # …and prose below a heading still passes, so the fix is a boundary and not a refusal
    # of everything that follows a heading.
    _refuse_an_approval_record_with_no_resolvable_home(
        _NO_APPROVAL_RECORD_AFTER_A_HEADING_LINE)
    # ⛔ AND THE TABLE ROW IS THE DECLARED FAIL-OPEN (review): green, so a later lane that
    # teaches the paragraph model table blocks has to re-decide this row, not discover it.
    _refuse_an_approval_record_with_no_resolvable_home(_APPROVAL_RECORD_IN_A_TABLE_ROW)
    # ⛔ …AND ITS TWO SIBLING BLOCK KINDS, MEASURED (review P3: this file said the row was
    # "the one block kind" the paragraph model does not know, which was false, and it sent
    # the next fix at the STRIPPER — the half that cannot reach either of these). Both are
    # green here for the same reason: the block's own line fuses with the label's, so the
    # paragraph does not open with the run. Pinned so the shapes are measured, not assumed,
    # and so a lane that closes them (via an END-of-paragraph boundary in
    # `_quoted_paragraphs`, NOT via `_PARAGRAPH_START_RE`'s starts, measured) has to
    # re-decide these two rows rather than discover them by accident.
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_RECORD_AFTER_A_THEMATIC_BREAK)
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_RECORD_AFTER_A_FENCED_BLOCK)
    _refuse_an_approval_record_with_no_resolvable_home(_NO_APPROVAL_RECORD_IN_BOLD_PROSE)
    # …and the negative sentence that DOES carry a colon, kept from the sibling lane's round
    # (@ 9d34e510, finding 5): the colon-requiring cut claimed the colon was what excluded
    # a denial, so this shape — which has one and records nothing — is the measurement that
    # the boundary is the BOLD OPENING RUN, not the punctuation.
    _refuse_an_approval_record_with_no_resolvable_home(_NO_APPROVAL_RECORD_WITH_A_COLON)
    # ⛔ THE CORPUS NUMBERS THE ARM'S DOCSTRING PROMISES, RE-DERIVED (#13669's acceptance
    # criterion): 8 landed blocks open with a bold approval label, 6 cite in that label's
    # sentence, and the 2 refusals are the landed v3.35 and v3.33 entries. A corpus edit
    # or a detector drift reds here instead of leaving the docstring claiming a
    # measurement nothing performs.
    labelled_versions: list[str] = []
    refused_versions: list[str] = []
    for landed_block in _block_bodies(
            ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")):
        labelled = [paragraph for paragraph in _quoted_paragraphs(landed_block)
                    if _opening_approval_label(paragraph)]
        if not labelled:
            continue
        version_match = _BLOCK_VERSION_RE.match(landed_block)
        assert version_match, "a labelled block must carry a readable version header"
        labelled_versions.append(version_match.group(1))
        # ⛔ THE REFUSAL IS READ FROM THE ARM ITSELF, NOT RE-IMPLEMENTED (review P3):
        # this loop used to re-decide with its own ``_CITATION_RE.search(_sentences(...))``
        # copy, so the message could claim "the landed entries this arm refuses changed"
        # while being blind to every change in the ARM — a second refusal condition, a
        # ``records[:1]`` narrowing, even dropping the arm's assert — because none of them
        # touches ``_CITATION_RE``. A second copy of a decision is the defect this file
        # refuses at ``:1395-1399`` ("Re-spelling one in the test would create a SECOND
        # literal copy, which the scan itself refuses"). Calling the arm makes the pin
        # measure the thing its message names.
        try:
            _refuse_an_approval_record_with_no_resolvable_home(landed_block)
        except AssertionError:
            refused_versions.append(version_match.group(1))
    assert labelled_versions == ["3.38", "3.37", "3.36", "3.35", "3.34",
                                 "3.33", "3.33", "3.32"], (
        "the set of landed blocks that OPEN with a bold approval label changed — the "
        f"arm's docstring states this measurement; read: {labelled_versions}")
    assert sorted(refused_versions) == ["3.33", "3.35"], (
        "the landed entries this arm refuses changed — its docstring names v3.35 (cites "
        f"nothing) and v3.33 (cites an issue, not a comment); read: {refused_versions}")
    # ⛔ THE TWO RETAINED NUMERALS ARE RE-DERIVED TOO (#13669 cycle 5): the unconditional
    # demand's 36 and the claim that no <b>/<strong> label appears in the corpus.
    landed_blocks = _block_bodies(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
    assert len([landed for landed in landed_blocks
                if not _CITATION_RE.search(_unfold_block(landed))]) == 36, (
        "the unconditional demand's false-block count changed (the arm's docstring says "
        "36 of the 43 landed blocks carry no comment id)")
    landed_text = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")
    assert "<b>" not in landed_text and "<strong>" not in landed_text, (
        "the corpus now writes an HTML label — the arm's docstring states it never does, "
        "which is why an HTML label is a declared fail-OPEN rather than a handled shape")
    # NON-VACUITY: both shapes this release shipped are refused, verbatim.
    with pytest.raises(AssertionError) as refused:
        _refuse_an_approval_record_with_no_resolvable_home(_PREFIX_APPROVAL_RECORD)
    _assert_refused_by(refused, "_refuse_an_approval_record_with_no_resolvable_home")
    # …and the same SHIPPED record in the five shapes a copy-edit produces — a doubled
    # space, emphasis on the label's last word, the label in `__…__`, and TITLE/ALL-CAPS
    # casing. Each RENDERS as the label and each was measured GREEN while the arm matched
    # the literal bytes, so a record with no home passed in all five (review @ e4927f5f
    # and @ 95a8df2f).
    for copy_edit in (_PREFIX_APPROVAL_RECORD_WITH_A_DOUBLED_SPACE,
                      _PREFIX_APPROVAL_RECORD_WITH_EMPHASIS_IN_THE_LABEL,
                      _PREFIX_APPROVAL_RECORD_IN_UNDERSCORE_EMPHASIS,
                      _PREFIX_APPROVAL_RECORD_IN_TITLE_CASE,
                      _PREFIX_APPROVAL_RECORD_IN_ALL_CAPS):
        with pytest.raises(AssertionError) as rendered:
            _refuse_an_approval_record_with_no_resolvable_home(copy_edit)
        _assert_refused_by(
            rendered, "_refuse_an_approval_record_with_no_resolvable_home")
    # ⛔ THE THREE LANDED SHAPES THE SHIPPED DETECTOR COULD NOT SEE (#13669 finding 1),
    # each refused and each attributed: a bold label with NO colon, the landed v3.32
    # shape with its comment id deleted (an ISSUE reference is not the approval's words),
    # and the landed v3.33 `**Approval**` label the shipped detector never examined.
    for unread_by_the_shipped_detector in (_APPROVAL_LABELLED_WITHOUT_A_COLON,
                                           _APPROVAL_CITING_AN_ISSUE_NOT_A_COMMENT,
                                           _APPROVAL_IN_A_BARE_BOLD_LABEL,
                                           _PREFIX_APPROVAL_RECORD_IS_ON_RECORD):
        with pytest.raises(AssertionError) as colonless:
            _refuse_an_approval_record_with_no_resolvable_home(
                unread_by_the_shipped_detector)
        _assert_refused_by(
            colonless, "_refuse_an_approval_record_with_no_resolvable_home")
    # ⛔ …and the failure TYPE is part of the direction (#13669 finding 3): a label
    # sentence ending at a full stop raised a bare `StopIteration`, so a lane saw a
    # traceback with no diagnosis. The refusal must be an AssertionError with the arm's
    # message on it.
    with pytest.raises(AssertionError) as full_stop:
        _refuse_an_approval_record_with_no_resolvable_home(
            _APPROVAL_LABEL_SENTENCE_ENDS_AT_A_FULL_STOP)
    _assert_refused_by(
        full_stop, "_refuse_an_approval_record_with_no_resolvable_home")
    assert NO_RESOLVABLE_HOME_DIAGNOSIS in str(full_stop.value), (
        "the refusal must carry a diagnosis a lane can act on, not a bare traceback")
    # …and the same label with its emphasis RUN CUT BY a sentence end (kept from the sibling
    # lane's round): the run closes in a later sentence, so the label sentence still owes a
    # citation — refused by THIS arm with its own diagnosis, never by a lookup exception.
    with pytest.raises(AssertionError) as straddling:
        _refuse_an_approval_record_with_no_resolvable_home(
            _APPROVAL_LABEL_STRADDLING_A_SENTENCE_END)
    _assert_refused_by(
        straddling, "_refuse_an_approval_record_with_no_resolvable_home")
    assert NO_RESOLVABLE_HOME_DIAGNOSIS in str(straddling.value), (
        "the straddling label must be refused by this arm's diagnosis, not by a "
        f"neighbouring assertion: {straddling.value}")
    # ⛔ AND THE MARKDOWN SHAPES A CHANGELOG ACTUALLY USES (review round 2): a bullet, an
    # ordered item, `*…*` and `_…_` all render as a labelled paragraph, and each was
    # measured ACCEPTED before the detector allowed a list marker and single-marker runs.
    for marked_up_label in (_APPROVAL_RECORD_AS_A_BULLET,
                            _APPROVAL_RECORD_AS_AN_ORDERED_ITEM,
                            _APPROVAL_RECORD_AS_AN_ASTERISK_BULLET,
                            _APPROVAL_RECORD_AS_A_PLUS_BULLET,
                            _APPROVAL_RECORD_AS_A_PARENTHESISED_ITEM,
                            _APPROVAL_RECORD_BEHIND_A_CHECKED_TASK_LIST_BOX,
                            _APPROVAL_RECORD_AS_A_LATER_BULLET,
                            _APPROVAL_RECORD_AS_A_LATER_ORDERED_ITEM,
                            _APPROVAL_RECORD_AS_A_LATER_ASTERISK_BULLET,
                            _APPROVAL_RECORD_AS_A_LATER_PLUS_BULLET,
                            _APPROVAL_RECORD_BEHIND_A_HEADING_AFTER_AN_ITEM,
                            _APPROVAL_RECORD_BEHIND_A_H4_HEADING_AFTER_AN_ITEM,
                            _APPROVAL_RECORD_AS_A_LATER_PARENTHESISED_ITEM,
                            _APPROVAL_RECORD_BEHIND_AN_H1_HEADING_AFTER_AN_ITEM,
                            _APPROVAL_RECORD_BEHIND_A_CAPITAL_X_TASK_LIST_BOX,
                            _APPROVAL_RECORD_IN_SINGLE_ASTERISK_EMPHASIS,
                            _APPROVAL_RECORD_IN_SINGLE_UNDERSCORE_EMPHASIS,
                            _APPROVAL_RECORD_BEHIND_A_HEADING,
                            _APPROVAL_RECORD_BEHIND_A_TASK_LIST_CHECKBOX,
                            _APPROVAL_RECORD_WITH_A_PLURAL_POSSESSIVE,
                            _APPROVAL_RECORD_WITH_A_SINGULAR_POSSESSIVE,
                            _APPROVAL_RECORD_WITH_A_TYPOGRAPHIC_APOSTROPHE,
                            _APPROVAL_RECORD_AS_A_BOLD_DENIAL,
                            _TOPICAL_APPROVAL_MENTION_AS_A_BOLD_LABEL,
                            # ⛔ THE DETERMINER AND LEAD-IN SHAPES, REFUSED (#13669):
                            # each was measured ACCEPTED before the start pattern read a
                            # determiner and a bounded lead-in, so each reds on its own
                            # narrowing — not as a group.
                            _APPROVAL_RECORD_WITH_A_LEADING_DETERMINER,
                            _APPROVAL_RECORD_WITH_AN_INDEFINITE_DETERMINER,
                            _APPROVAL_RECORD_WITH_A_RELEASE_ID_LEAD_IN,
                            _APPROVAL_RECORD_WITH_A_LEADING_DASH,
                            _TOPICAL_APPROVAL_MENTION_WITH_AN_ARTICLE):
        with pytest.raises(AssertionError) as marked_up:
            _refuse_an_approval_record_with_no_resolvable_home(marked_up_label)
        _assert_refused_by(
            marked_up, "_refuse_an_approval_record_with_no_resolvable_home")
    # ⛔ THE LIST SPLIT'S MIRROR DIRECTION (review round 5): the later item is READ, so a
    # later item that records no approval must still pass — otherwise "refuse every list"
    # would satisfy the two rows above.
    _refuse_an_approval_record_with_no_resolvable_home(
        _A_LATER_BULLET_WITH_NO_APPROVAL_RECORD)
    # ⛔ EVERY RECORD IS CHECKED (review round 2): a cited first record does not discharge
    # a second, uncited one in the SAME block — measured, checking `records[:1]` left this
    # GREEN — and two cited records stay green, so the loop is not a blanket refusal.
    with pytest.raises(AssertionError) as second_record:
        _refuse_an_approval_record_with_no_resolvable_home(_TWO_RECORDS_ONE_UNCITED)
    _assert_refused_by(
        second_record, "_refuse_an_approval_record_with_no_resolvable_home")
    _refuse_an_approval_record_with_no_resolvable_home(_TWO_RECORDS_BOTH_CITED)
    # ⛔ THE NESTED-QUOTE DIRECTIONS (review round 4), both measured: a quoted approval
    # that carries its own id PASSES, one that does not is REFUSED — the per-record loop,
    # not the multiplicity assert the test docstring used to promise.
    _refuse_an_approval_record_with_no_resolvable_home(
        _NESTED_QUOTED_APPROVAL_WITH_ITS_OWN_ID)
    # …and the declared HTML fail-OPEN is ACCEPTED, which is what the residual list says
    # (#13669 cycle 5): an HTML-bold label is not an emphasis run to this reader.
    _refuse_an_approval_record_with_no_resolvable_home(_HTML_LABEL_IS_NOT_A_RECORD)
    _refuse_an_approval_record_with_no_resolvable_home(_ENTITY_ENCODED_LABEL_IS_NOT_A_RECORD)
    # ⛔ THE WIDENING'S MIRRORS, GREEN (#13669): a determiner or a lead-in must not be the
    # word that refuses a record which DOES name its comment — otherwise "refuse every run
    # starting with `The` or a dash" would satisfy the four rows above.
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_RECORD_WITH_A_LEADING_DETERMINER_AND_AN_ID)
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_RECORD_WITH_A_RELEASE_ID_LEAD_IN_AND_AN_ID)
    # …and the BOUND of the lead-in rule is a measured residual, not a silent one.
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_RECORD_WITH_AN_UNREAD_LEAD_IN)
    # ⛔ AND THE MID-PARAGRAPH BOUNDARY, BOTH DIRECTIONS (merge reconciliation with `main`'s
    # #13679, whose paragraph-wide scan read these): a label that does not OPEN its
    # paragraph is out of scope to this reader, so both PASS. Pinned so a widening has to
    # meet the trade (a bolded mid-sentence mention becoming a false block) rather than
    # assume it.
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_LABEL_AFTER_AN_UNBOLDED_MENTION)
    _refuse_an_approval_record_with_no_resolvable_home(
        _PREFIX_APPROVAL_RECORD_WITH_AN_UNBOLDED_MENTION_CITING_AN_ID)
    # …and the label split across two emphasis runs is the other declared fail-OPEN, so it
    # is ACCEPTED (review round 8).
    _refuse_an_approval_record_with_no_resolvable_home(_APPROVAL_LABEL_SPLIT_ACROSS_RUNS)
    with pytest.raises(AssertionError) as nested_without_an_id:
        _refuse_an_approval_record_with_no_resolvable_home(
            _NESTED_QUOTED_APPROVAL_WITHOUT_AN_ID)
    _assert_refused_by(
        nested_without_an_id, "_refuse_an_approval_record_with_no_resolvable_home")
    # …and the CITED ordered item is GREEN (review round 6): the marker strip is what keeps
    # `_sentences` from cutting the label sentence at the marker's own full stop.
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_RECORD_AS_A_CITED_ORDERED_ITEM)
    # …and the NAMED COST of the shared splitter is measured, not merely claimed (review
    # round 6): a cited label sentence cut by an initial is refused.
    with pytest.raises(AssertionError) as cut_by_an_initial:
        _refuse_an_approval_record_with_no_resolvable_home(
            _APPROVAL_RECORD_CUT_BY_AN_INITIAL)
    _assert_refused_by(
        cut_by_an_initial, "_refuse_an_approval_record_with_no_resolvable_home")
    # NON-VACUITY, the F2 direction: an id that is NOT the approval's — the landed
    # record with its own relay deleted, only the incidental reference left. Pre-fix
    # this was GREEN (a paragraph-wide search found the borrowed id), so the arm could
    # be satisfied by a record that points only at the form the owner rejected.
    with pytest.raises(AssertionError) as borrowed:
        _refuse_an_approval_record_with_no_resolvable_home(
            _APPROVAL_CITING_A_LATER_SENTENCE)
    _assert_refused_by(
        borrowed, "_refuse_an_approval_record_with_no_resolvable_home")
    # …and again where the sentence is closed by a QUOTATION (v3.38's own shape), so
    # the boundary must carry the closing run and not only `[.;!?]`:
    with pytest.raises(AssertionError) as borrowed_after_a_quote:
        _refuse_an_approval_record_with_no_resolvable_home(
            _APPROVAL_CITING_A_LATER_SENTENCE_AFTER_A_QUOTATION)
    _assert_refused_by(
        borrowed_after_a_quote, "_refuse_an_approval_record_with_no_resolvable_home")
    # …and the RESOLVED shape is green, so the arm above is not satisfied by refusing
    # every record that names an id anywhere — including where the id follows an
    # abbreviation, which the splitter used to cut off at `e.g.`'s dot (measured refusal
    # of true prose, review @ 95a8df2f).
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_RELAY_IN_LABEL_SENTENCE)
    _refuse_an_approval_record_with_no_resolvable_home(
        _APPROVAL_CITING_AFTER_AN_ABBREVIATION)
    with pytest.raises(AssertionError) as pointer:
        _refuse_an_unresolvable_pointer(_PREFIX_UNRESOLVED_POINTER)
    _assert_refused_by(pointer, "_refuse_an_unresolvable_pointer")
    # …and again where a SOFT WRAP splits the phrase (measured fail-open before the arm
    # unfolded first): the pointer is the same, only the reflow differs.
    with pytest.raises(AssertionError) as wrapped_pointer:
        _refuse_an_unresolvable_pointer(_POINTER_SPLIT_BY_A_SOFT_WRAP)
    _assert_refused_by(wrapped_pointer, "_refuse_an_unresolvable_pointer")
    # …and again where the sentence is closed by a QUOTATION and the number in the
    # sentence after it is borrowed (measured fail-open with a naive sentence split):
    with pytest.raises(AssertionError) as borrowed_pointer:
        _refuse_an_unresolvable_pointer(
            _POINTER_BORROWING_AN_ID_ACROSS_A_QUOTE_BOUNDARY)
    _assert_refused_by(borrowed_pointer, "_refuse_an_unresolvable_pointer")
    # …and the two shapes a COPY-EDIT produces — a doubled space and an emphasis run —
    # which are the same pointer to a reader (measured fail-open before the arm read
    # the rendered text):
    with pytest.raises(AssertionError) as spaced_pointer:
        _refuse_an_unresolvable_pointer(_POINTER_WITH_A_DOUBLED_SPACE)
    _assert_refused_by(spaced_pointer, "_refuse_an_unresolvable_pointer")
    with pytest.raises(AssertionError) as emphasised_pointer:
        _refuse_an_unresolvable_pointer(_POINTER_WITH_AN_EMPHASIS_RUN)
    _assert_refused_by(emphasised_pointer, "_refuse_an_unresolvable_pointer")
    # …and again where the pointer ends without terminal punctuation and the NEXT
    # PARAGRAPH carries the id (measured fail-open while the whole block was unfolded
    # as one run): the id belongs to a different sentence, so it is not a resolution.
    with pytest.raises(AssertionError) as paragraph_pointer:
        _refuse_an_unresolvable_pointer(_POINTER_ACROSS_A_PARAGRAPH_BREAK)
    _assert_refused_by(paragraph_pointer, "_refuse_an_unresolvable_pointer")
    # …and the ordinary copy-edits of the PHRASE ITSELF, each measured ACCEPTED before the
    # arm stopped requiring adjacency and one casing (review @ 95a8df2f):
    for reworded in (_POINTER_WITH_A_QUALIFIED_ISSUE,
                     _POINTER_IN_THE_PLURAL,
                     _POINTER_CAPITALISED):
        with pytest.raises(AssertionError) as reworded_pointer:
            _refuse_an_unresolvable_pointer(reworded)
        _assert_refused_by(reworded_pointer, "_refuse_an_unresolvable_pointer")
    # ⛔ THE POINTER IN THE OTHER WORD ORDER (#13669 finding 2), bare and qualified,
    # each refused and attributed to this arm:
    for reordered_pointer_fixture in (_POINTER_IN_THE_OTHER_WORD_ORDER,
                                      _POINTER_QUALIFIED_IN_THE_OTHER_WORD_ORDER,
                                      _POINTER_WITH_AN_ALL_OF_IDIOM,
                                      _POINTER_WITH_A_TWO_WORD_WINDOW):
        with pytest.raises(AssertionError) as reordered_pointer:
            _refuse_an_unresolvable_pointer(reordered_pointer_fixture)
        _assert_refused_by(reordered_pointer, "_refuse_an_unresolvable_pointer")
    # …and the MIRROR direction: a semicolon is not a sentence break to a reader, so a
    # resolved pointer after one is ONE sentence and stays green (measured refusal of
    # true prose while `;` was a terminator).
    _refuse_an_unresolvable_pointer(_POINTER_RESOLVED_AFTER_A_SEMICOLON)
    # …and the resolved MIRROR of the reversed order stays green (kept from the sibling
    # lane's round): a pointer that gives its number is not a pointer with no home, in
    # either word order.
    _refuse_an_unresolvable_pointer(_POINTER_RESOLVED_WITH_THE_ISSUE_FIRST)
    # …and a RESOLVED pointer is not refused, so the arm cannot be satisfied by
    # refusing every sentence that mentions an issue's existence.
    _refuse_an_unresolvable_pointer(
        "> reads four conditions). That is a code defect with its own issue, #13450; "
        "it is not a sentence to rewrite.")
    # ⛔ AND NOT EVERY SENTENCE WITH `issue` BEFORE `own` IS A POINTER (review round 2):
    # measured, an unbounded reversal refused all three of these TRUE sentences.
    for not_a_pointer in (_NOT_A_POINTER_ON_ITS_OWN_MERITS,
                          _NOT_A_POINTER_WITH_ITS_OWN_SECTION,
                          _NOT_A_POINTER_WITH_ITS_OWN_SCOPE,
                          _NOT_A_POINTER_OF_OUR_OWN_MAKING,
                          _NOT_A_POINTER_OF_THEIR_OWN_DESIGN,
                          _NOT_A_POINTER_OF_ITS_OWN_ACCORD):
        _refuse_an_unresolvable_pointer(not_a_pointer)
    # ⛔ EVERY HEAD IN THE STOP-LIST IS DRIVEN (review round 4): a class member with no
    # fixture is a narrowing no measure can see — deleting these seven left the suite
    # GREEN, so the list is iterated here rather than trusted.
    for idiom_head in ("behalf", "devising", "merits", "right", "sake", "terms",
                       "volition"):
        _refuse_an_unresolvable_pointer(
            f"> That was an issue of our own {idiom_head}.")
    # ⛔ AND THE FORWARD FORM NAMING A CONTAINER IS REFUSED (#13669 cycle 5), the cost the
    # arm's docstring now names: the stop-list binds the reversed form only.
    with pytest.raises(AssertionError) as container_pointer:
        _refuse_an_unresolvable_pointer(_POINTER_NAMING_A_CONTAINER)
    _assert_refused_by(container_pointer, "_refuse_an_unresolvable_pointer")
    # ⛔ AND THE MERGED READER'S NEW FALSE BLOCK IS DRIVEN (cycle 7): a pointer resolved by
    # the NEXT list item is refused, which the pointer docstring names.
    with pytest.raises(AssertionError) as next_item_pointer:
        _refuse_an_unresolvable_pointer(_POINTER_RESOLVED_IN_THE_NEXT_LIST_ITEM)
    _assert_refused_by(next_item_pointer, "_refuse_an_unresolvable_pointer")


def test_the_block_kind_residuals_are_fail_open_by_a_commonmark_reading() -> None:
    """The block kinds the paragraph model does not name — MEASURED, not read off prose.

    Review P3 at ``3b62664120``: this file called the table row "the one block kind
    ``_PARAGRAPH_START_RE`` does not know", which is false — a thematic break and a fenced
    block are two more, and the label after either DOES open a paragraph to a reader — the
    same fail-OPEN DIRECTION as the row, by a DIFFERENT mechanism: the row's label sits
    mid-paragraph behind the table's ``|`` and opens nothing, which the assertions below
    state as its own shape rather than folding it into the break's. The completeness word
    mattered because it points the next fix at the wrong half: the row closes through the
    STRIPPER, while these two close only through an END-of-paragraph boundary in
    ``_quoted_paragraphs``.

    Two facts are derived here, and neither is asserted in prose alone:

    * a real CommonMark parser gives a reader-visible paragraph carrying the label in all
      three shapes, though NOT the same way: the break and the fence make the label OPEN
      its paragraph, while the row makes one paragraph that OPENS with ``|`` and carries
      the label mid-paragraph. That difference is measured below rather than asserted in
      prose, and it is what makes the green pin above an honest DECLARED gap rather than
      the right answer stated as a residual;
    * the arm does NOT read any of the three — so the day one is closed, this test reds and
      says to move that shape to the refused set, instead of leaving a pin that is now a
      false claim.
    """
    from markdown_it import MarkdownIt

    parser = MarkdownIt("commonmark")
    shapes = {
        "table row": _APPROVAL_RECORD_IN_A_TABLE_ROW,
        "thematic break": _APPROVAL_RECORD_AFTER_A_THEMATIC_BREAK,
        "fenced block": _APPROVAL_RECORD_AFTER_A_FENCED_BLOCK,
    }
    for kind, block in shapes.items():
        tokens = parser.parse(block)
        label_paragraphs = [tokens[index + 1].content
                            for index, token in enumerate(tokens[:-1])
                            if token.type == "paragraph_open"]
        # ⛔ THE PROPERTY, NOT A SUBSTRING (review P2 at `0e9f920d7f6`): "some paragraph
        # CONTAINS the label" is satisfied by the ROW's own paragraph, whose text OPENS
        # with `|` — so a containment assert passed for a reason that contradicts the
        # comment it documents. The groups differ in exactly this, so each is asserted
        # with its own shape and neither can stand in for the other.
        opened = [p for p in label_paragraphs if _opening_approval_label(p)]
        if kind == "table row":
            assert label_paragraphs and all(p.lstrip().startswith("|")
                                            for p in label_paragraphs), (
                f"a reader gives the table row ONE paragraph whose text OPENS with `|`; "
                f"measured rendered paragraphs: {label_paragraphs!r}")
            assert not opened, (
                f"the table row's label now OPENS its paragraph — that is the STRIPPER "
                f"having learned the row, so this shape is no longer the fail-open this "
                f"test declares: measured paragraphs: {label_paragraphs!r}")
        else:
            assert opened, (
                f"a reader opens a paragraph for the label AFTER a {kind} — i.e. the "
                f"label OPENS it; that is what makes the green pin a DECLARED gap; "
                f"measured rendered paragraphs: {label_paragraphs!r}")
        assert not [paragraph for paragraph in _quoted_paragraphs(block)
                    if _opening_approval_label(paragraph)], (
            f"the {kind} shape is READ as a record now — that is an improvement, but it is "
            f"no longer a residual: move it to the refused set and out of the residual "
            f"list instead of leaving it pinned green")
    residual_list = _refuse_an_approval_record_with_no_resolvable_home.__doc__ or ""
    for constant in ("_APPROVAL_RECORD_IN_A_TABLE_ROW",
                     "_APPROVAL_RECORD_AFTER_A_THEMATIC_BREAK",
                     "_APPROVAL_RECORD_AFTER_A_FENCED_BLOCK"):
        assert constant in residual_list, (
            f"the residual list must name {constant}: it is what a lane reads before "
            f"choosing a fix for this shape, so a shape pinned green while unnamed is the "
            f"silent gap the list exists to prevent")


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
    # ⛔ The §4.3-quoting block is located by SEARCH, not by position: a newer release may
    # legitimately not touch §4.3 (measured — the v3.35 entry is about §5), and reading
    # `blocks[0]` then asserted against a block that quotes nothing.
    block = next((b for b in _block_bodies(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
                  if _quoted_clause(b) is not None), None)
    assert block is not None, (
        "the landed block no longer records the §4.3 clause, so this arm's only "
        "accepting direction cannot be exercised against it")
    identifiers = _citation_ids(block)
    assert identifiers, (
        "the landed block carries no comment id, so there is no citation to build "
        "the rejected shapes from")
    identifier = identifiers[0]
    # Padding at least as long as the entry this arm guards, so a denial placed at
    # the far end of the fixture is beyond ANY window a future cut might introduce.
    # ⛔ THE PADDING MUST END ON A WORD BOUNDARY. Truncating `"padding "` at an
    # arbitrary offset can cut it mid-word, and the fixture's denial is then glued to
    # the stub — `…dding pa` + `No approval was recorded` reads as `paNo approval`,
    # whose `\bno` boundary is gone, so the arm "refused nothing" for a fixture that
    # was simply malformed. Measured: a 6-character edit to an unrelated sentence of
    # the landed block flipped this pin red. The trailing space is part of the fixture.
    entry_scale_padding = ("padding " * (len(block) // 8 + 2))[:len(block)] + " "
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
    # ⛔ Located by SEARCH, not position: a newer release may not touch §4.3 at all
    # (measured — the v3.35 entry is about §5 and cites no comment id).
    identifier = _citation_ids(
        next(b for b in _block_bodies(ONTOLOGY_CHANGELOG.read_text(encoding="utf-8"))
             if _quoted_clause(b) is not None))[0]
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


# ── a positional changelog pointer must still identify a block ─────────────


_CHANGELOG_LINE_POINTER_RE = re.compile(r"ONTOLOGY-CHANGELOG\.md:(\d+)")


def test_every_positional_changelog_pointer_lands_on_a_release_header() -> None:
    """A positional pointer is only an IDENTITY if it lands on a block's header.

    Successive review rounds on #13404 found the same class repeatedly: a line
    pointer into `docs/ONTOLOGY-CHANGELOG.md` left un-re-based by a release that
    inserted a block above it, and NOTHING reds when that happens, because this
    suite resolves citations by CONTENT and a moved number still parses. The
    measured worst case is why "just renumber it" is not enough: one pointer had
    already been stale on `main` BEFORE the PR re-based it, so the arithmetic
    that looked correct landed a second wrong number.

    So the invariant is not "renumber carefully" — it is that a number must at
    least still identify a BLOCK. A pointer that resolves to a release header
    names something a reader can follow even after an insertion; a pointer that
    resolves to a sentence inside some block (or to the CRITICAL RULE banner)
    names nothing recoverable. The durable form is the one this repo already
    records (`docs/00_index.md`: cited by SYMBOL, not by line, because a symbol
    cannot re-stale): cite the release (``v3.NN``) or the permalink of the
    comment the block quotes. A positional pointer is tolerated only where a pin
    requires one for a block's IDENTITY (the SCOPE pin above reads
    `docs/ONTOLOGY-CHANGELOG.md:<line>` beside the issue that block records), and
    even there it must resolve to a release header.

    ⛔ STATED LIMIT: this pin resolves any `ONTOLOGY-CHANGELOG.md:<line>` pointer, with
    or without the `docs/` prefix — the pattern is not anchored to `docs/`, and
    saying otherwise was false of it. A bare `:NNN` shorthand names no file, so
    the module text alone cannot resolve it, and it is not read here; a line
    pointer into a DIFFERENT document has its own resolver
    (`tests/test_status_as_reported_single_declaration_10524.py` covers the
    `ONTOLOGY.md` idiom pointer). Widening this arm to the shorthand needs the
    enclosing sentence's file reference, not a bigger regex.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    changelog = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8").splitlines()
    pointers = _CHANGELOG_LINE_POINTER_RE.findall(source)
    # ⛔ A SWEEP THAT CAN FIND NOTHING MUST NOT PASS. `bad == []` is also what a
    # module with NO positional pointer produces, so deleting the last guarded
    # pointer would leave this pin green with nothing checked — and this very PR
    # converts pointers away from the positional form, which is the edit that
    # reaches that state. Fail closed instead, and name the alternative.
    assert pointers, (
        "no `docs/ONTOLOGY-CHANGELOG.md:<line>` pointer is left in this module, "
        "so this pin checks nothing. Restate the claim by release or permalink "
        "AND keep one positional pointer for a block's identity (the SCOPE pin "
        "above needs one), or delete this pin deliberately.")
    bad = []
    for pointer in pointers:
        line_no = int(pointer)
        text = (changelog[line_no - 1]
                if 0 < line_no <= len(changelog) else "")
        if _BLOCK_VERSION_RE.search(text) is None:
            bad.append(f":{pointer} reads {text.strip()[:70]!r}")
    assert bad == [], (
        "a positional pointer into the changelog no longer lands on a RELEASE "
        "HEADER, so it identifies no block a reader can recover: "
        + "; ".join(bad)
        + ". Cite the release (`v3.NN`) or the permalink of the comment the "
        "block quotes — a number that points at prose inside a block rots on "
        "the next insertion and nothing else in this suite reds.")
