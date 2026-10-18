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
import json
import re
import subprocess
import sys
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
#: ``rebuild_all`` is excluded separately — it is the whole-schema writer
#: (``SET s += $props``) and is declared as such.
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


# ── the recorded decisions must have a real home ───────────────────────────

ONTOLOGY = REPO_ROOT / "docs/ONTOLOGY.md"
# The changelog prose moved out of `ONTOLOGY.md` into its sibling (#8745), so a
# `Changelog vX.Y` header is read from there — the model file no longer carries
# one, and pinning the header's home to `ONTOLOGY.md` would fail on a document
# that is correct.
ONTOLOGY_CHANGELOG = REPO_ROOT / "docs/ONTOLOGY-CHANGELOG.md"
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


def test_section_4_8_changelog_entry_sits_under_its_own_version_block() -> None:
    """The §4.8 changelog bullet belongs to its own version/date/issue header.

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

    So this asserts the RELATION, not the literal: the bullet sits under the
    NEWEST block, and that block's version is the version the model document
    itself declares. Pre-fix (both the original ``v3.23`` shape and the
    ``v3.24`` one) this fails.
    """
    changelog = ONTOLOGY_CHANGELOG.read_text(encoding="utf-8")
    ontology = ONTOLOGY.read_text(encoding="utf-8")
    bullet = changelog.index("§4.8 (added by #3554")
    header = changelog.rindex("> **Changelog v3.", 0, bullet)
    header_line = changelog[header:changelog.index("\n", header)]
    # The version the model document declares about itself (frontmatter title).
    declared = re.search(r'^title: "Tortoise — Canonical Ontology v(\d+\.\d+)"',
                         ontology, re.M)
    assert declared, "docs/ONTOLOGY.md must declare its model version in frontmatter"
    assert f"v{declared.group(1)}" in header_line, (
        f"§4.8 is recorded under {header_line!r} but the model document declares "
        f"v{declared.group(1)} — a new model section must be recorded in the "
        "release that carries it, not an older one")
    newest = changelog.index("> **Changelog v3.")
    newest_line = changelog[newest:changelog.index("\n", newest)]
    assert newest == header, (
        "§4.8 must be recorded in the NEWEST changelog block (this file is "
        f"newest-first); it sits under {header_line!r} while the newest block "
        f"is {newest_line!r}")
    assert "#3554" in header_line, (
        "the §4.8 block's header must name the issue whose section it records")
    # …and the header must not enumerate an issue that landed in another block.
    assert header_line.count("#") >= 1


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

    ``SESSION_PROPERTY_OWNERS["id"]`` names only ``rebuild_all`` while ten
    declared writers mint the node with ``MERGE (s:Session {id: …})`` — and the
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
