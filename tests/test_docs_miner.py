"""#1724 — deterministic docs → DRAFT statement Point mining.

Two halves:

* **Unit** (no graph): the extraction rule and the identity scheme. These pin
  WHAT becomes a claim (cue-gated, default-deny) and HOW a claim is keyed
  (content-addressed, so re-mining is idempotent).
* **Integration** (embedded FalkorDB): the write path — draft status,
  ``extractedFrom`` → the D10-folded Document Source, ``aboutObject`` → the
  repo entity resolved by its stable external anchor, and the two dedup
  mechanisms the issue's falsifier measures (re-mine ⇒ 0 new Points).

The falsifier named in #1724 — *"Re-mining an unchanged corpus ⇒ 0 new
Points"* — is ``test_remining_unchanged_corpus_reports_zero_new_points``. It
is a real re-run over the SAME corpus and the SAME graph, not a mock.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise.docs_miner import (
    CLAIM_KEY_LEN,
    EXTERNAL_ID_PREFIX,
    POINT_KIND_STATEMENT,
    REPO_OBJECT_KIND,
    claim_key,
    derive_repo_from_corpus,
    external_id,
    extract_claims,
    iter_claim_spans,
    mine_docs,
    normalize_claim,
    repo_external_id,
    repo_object_id,
    statement_id,
)

DOC_WITH_CLAIMS = """\
---
title: "The gate"
type: architectureDoc
summary: The gate must never be skipped for a document source.
---

# The gate

This paragraph is only prose and carries no normative cue at all.

```
a must line inside a fence is never a claim
```

- The write path **must** resolve the `Source` before any Point is written.
- [ ] a checkbox item must never become a claim

> A document is a Source, not a graph node.

**OVERRIDES:** the window -- the default was rejected for this case.

Decision: we choose the deterministic path for docs.

The old `doc_status` field is deprecated in favour of reading the entities.

| must | never |
"""

SESSION_FILE = """\
---
sessionId: s-1
title: "A session"
---
Speaker: hello there.
"""

DOC_SECOND = """\
---
title: "Second"
type: architectureDoc
---

- The second document must also produce exactly one draft statement.
"""


# ── unit: the extraction rule ─────────────────────────────────────

def test_normalize_claim_strips_markdown_but_keeps_identifiers():
    """Markdown-ism is normalized away; snake_case is NOT.

    A single ``_``/``*`` must survive: this corpus is full of identifiers
    (``mine_docs``, ``TORTOISE_ALLOW_MAIN_EDITS``), and rewriting one would
    change that claim's identity and therefore its Point.
    """
    assert normalize_claim("- The write path **must** resolve the `Source`.") == \
        "The write path must resolve the Source"
    assert normalize_claim("> `mine_docs` must never run in the main checkout") == \
        "mine_docs must never run in the main checkout"
    assert normalize_claim("[the gate](x.md) must hold") == "the gate must hold"


def test_extract_claims_is_cue_gated_and_skips_document_structure():
    """The whole extraction rule in one assertion: structure is skipped, and a
    sentence with no cue from any family is NOT a claim (default-deny).

    Concretely excluded in the fixture: the frontmatter body, a fenced code
    block that literally says "must never", a checkbox line that says "must
    never", a markdown table whose cells say "must"/"never", and a cue-less
    prose paragraph.
    """
    claims = extract_claims(DOC_WITH_CLAIMS, doc_id="doc_docs/gate.md")
    contents = [c["content"] for c in claims]
    cues = {c["cue"] for c in claims}

    assert cues == {"frontmatter", "normative", "decision", "lifecycle"}
    assert "The gate must never be skipped for a document source" in contents
    assert "The write path must resolve the Source before any Point is written" in contents
    assert "OVERRIDES: the window -- the default was rejected for this case" in contents
    assert "Decision: we choose the deterministic path for docs" in contents
    assert "The old doc_status field is deprecated in favour of reading the entities" in contents
    joined = " | ".join(contents)
    assert "inside a fence" not in joined
    assert "checkbox item" not in joined
    assert "no normative cue at all" not in joined


def test_claim_identity_is_content_addressed_and_stable():
    """Same assertion twice in one doc ⇒ ONE claim; a reword ⇒ a NEW key.

    This is the property that makes an unchanged re-mine merge instead of
    stack: the id is a pure function of the normalized content, so the second
    extraction derives the SAME id.
    """
    text = ("- The miner must never spend a token.\n\n"
            "- The miner must never spend a token.\n")
    claims = extract_claims(text, doc_id="doc_docs/a.md")
    assert len(claims) == 1
    again = extract_claims(text, doc_id="doc_docs/a.md")
    assert again[0]["id"] == claims[0]["id"]
    assert again[0]["key"] == claims[0]["key"]

    reworded = extract_claims("- The miner must never spend any token.\n",
                              doc_id="doc_docs/a.md")
    assert reworded[0]["key"] != claims[0]["key"]
    assert reworded[0]["id"] != claims[0]["id"]


def test_claim_id_and_external_id_carry_the_doc_identity():
    key = claim_key("the gate must hold")
    doc_id = "doc_docs/architecture/gate.md"
    assert statement_id(doc_id, key) == f"pt_doc_doc-docs-architecture-gate-md_{key}"
    assert external_id(doc_id, key) == f"{EXTERNAL_ID_PREFIX}{doc_id}#{key}"
    assert len(key) == CLAIM_KEY_LEN


def test_max_claims_cap_is_document_order_stable():
    text = "".join(f"- claim {i} must never be dropped\n" for i in range(10))
    first = extract_claims(text, doc_id="doc_docs/a.md", max_claims=4)
    second = extract_claims(text, doc_id="doc_docs/a.md", max_claims=4)
    assert len(first) == 4
    assert [c["id"] for c in first] == [c["id"] for c in second]
    assert [c["content"] for c in first] == [f"claim {i} must never be dropped"
                                             for i in range(4)]


def test_iter_claim_spans_reports_one_based_line_numbers():
    spans = iter_claim_spans("# T\n\nThe reader must never block.\n")
    assert spans and spans[0][1] == 3 and spans[0][2] == 3


def test_repo_anchor_is_derived_from_the_stable_external_id():
    assert repo_object_id("acme/repo1") == "github-repo-acme/repo1"
    assert repo_external_id("acme/repo1") == "github:repo:acme/repo1"


def test_external_id_is_a_declared_point_prop():
    """The statement contract's ``externalId`` is declared, so neither the live
    write nor a rebuild logs a false undeclared-prop drift warning (#2795).

    Same shape as the `goalState` pin in `tests/test_7856_goal_state.py`: the
    declaration lives in `_POINT_DECLARED_PROPS`, and without it the replay
    passthrough warns about a prop this repo's own writers always set.
    """
    from tortoise.projection.entities import _EntityHandlers

    assert "externalId" in _EntityHandlers._POINT_DECLARED_PROPS


def test_derive_repo_from_corpus_reads_the_staged_layout(tmp_path: Path):
    base = tmp_path / "ingest"
    staged = base / "org-1" / "repo-2"
    staged.mkdir(parents=True)
    assert derive_repo_from_corpus(staged, str(base)) == "org-1/repo-2"
    # a corpus that is NOT the staged shape is an honest None, never a guess
    assert derive_repo_from_corpus(tmp_path / "loose", str(base)) is None


# ── integration: the write path ───────────────────────────────────

@pytest.fixture
def corpus(tmp_path: Path, monkeypatch, force_sparse_tfidf, request):
    """A staged corpus (``<base>/<org>/<repo>``) + an SDK on a fresh DB.

    ``TORTOISE_INDEX_NO_NETWORK=1`` keeps the corpus pass off the embedding
    network path and ``force_sparse_tfidf`` pins the embedder OFF entirely —
    this suite measures the MINER, and a cold model load would add minutes of
    wall clock without touching anything under test.

    The NAMESPACE is per test, and that is load-bearing: on the URI lane a
    namespace maps to a VERBATIM server graph (no path hash), so a shared
    ``test_docs`` is ONE graph for the whole session — every test in this file
    would then see the Objects and Points the others mined, which is how a
    "the first run mined something" or "an Object already exists" failure can
    land on a graph no test of its own touched.
    """
    from tortoise.sdk import TortoiseSDK

    slug = re.sub(r"[^a-z0-9_]", "", request.node.name.lower())[:40]
    monkeypatch.setenv("TORTOISE_INDEX_NO_NETWORK", "1")
    base = tmp_path / "ingest"
    root = base / "org-1" / "repo-2"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "gate.md").write_text(DOC_WITH_CLAIMS, encoding="utf-8")
    (root / "docs" / "second.md").write_text(DOC_SECOND, encoding="utf-8")
    (root / "docs" / "session.md").write_text(SESSION_FILE, encoding="utf-8")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))

    sdk = TortoiseSDK(str(tmp_path / "docs.db"), namespace=f"test_docs_{slug}")
    yield sdk, root, base
    sdk.close()


def _graph(sdk):
    return sdk._get_proj().g


def _points(sdk):
    return _graph(sdk).query(
        "MATCH (p:Point) RETURN p.id, p.status, p.pointKind, p.extractedFrom, "
        "p.externalId"
    ).result_set


def test_mine_docs_writes_draft_statements_with_source_and_object_wiring(corpus):
    sdk, root, _base = corpus
    report = mine_docs(sdk, str(root))

    # the repo derives from the staged <base>/<org>/<repo> layout
    assert report["repo"] == "org-1/repo-2"
    # the session file is left to its own miner
    assert report["skipped_not_document"] == 1
    assert report["documents"] == 2
    assert report["new_points"] == report["claims"] > 0
    assert report["errors"] == []

    g = _graph(sdk)
    rows = _points(sdk)
    assert rows, "the miner wrote no Points"
    for pid, status, kind, extracted_from, ext in rows:
        assert pid and ext and extracted_from  # identity is never blank
        assert status == "draft"            # reviewable, never live
        assert kind == POINT_KIND_STATEMENT  # "claim" is a gloss, not a kind
        assert extracted_from.startswith("doc_docs/")
        assert ext.startswith(EXTERNAL_ID_PREFIX)

    # extractedFrom → the D10-folded Document Source (url = doc_<rel-path>)
    edges = g.query(
        "MATCH (p:Point)-[:extractedFrom]->(s:Source) RETURN count(p)"
    ).result_set
    assert edges[0][0] == len(rows)
    for _, _, _, extracted_from, _ in rows:
        found = g.query("MATCH (s:Source {url:$u}) RETURN s.documentKind",
                        params={"u": extracted_from}).result_set
        assert found, f"no Document Source for {extracted_from}"

    # aboutObject → the repo Object, resolved by its stable external anchor
    objects = g.query(
        "MATCH (o:Object) WHERE o.externalId = $ext RETURN o.id, o.name",
        params={"ext": repo_external_id("org-1/repo-2")},
    ).result_set
    assert objects and objects[0][0] == repo_object_id("org-1/repo-2")
    wired = g.query(
        "MATCH (p:Point)-[:aboutObject]->(o:Object) RETURN count(p)"
    ).result_set
    assert wired[0][0] == len(rows)

    # no Subject stubs were minted by the entity path (mirrors W-1)
    assert not g.query("MATCH (n:Subject) RETURN n LIMIT 1").result_set


def test_remining_unchanged_corpus_reports_zero_new_points(corpus):
    """#1724's own falsifier: re-mining an unchanged corpus ⇒ 0 new Points."""
    sdk, root, _base = corpus
    first = mine_docs(sdk, str(root))
    assert first["new_points"] > 0

    points_after_first = len(_points(sdk))
    second = mine_docs(sdk, str(root))

    assert second["new_points"] == 0
    assert second["existing_points"] == 0
    assert second["skipped_unchanged"] == first["documents"]
    assert second["mined"] == 0
    # nothing was written, read or duplicated: same Point count, same edges
    assert len(_points(sdk)) == points_after_first
    assert _graph(sdk).query(
        "MATCH (p:Point)-[:aboutObject]->(o:Object) RETURN count(p)"
    ).result_set[0][0] == points_after_first


def test_edited_doc_mines_the_new_claim_and_reuses_the_untouched_one(corpus):
    """Hash-dedup must not over-skip, and deterministic keying must not churn.

    One claim is left byte-identical (it must NOT be re-minted as new) and one
    is added, so the run reports exactly one new Point.
    """
    sdk, root, _base = corpus
    mine_docs(sdk, str(root))
    before = len(_points(sdk))

    doc = root / "docs" / "gate.md"
    doc.write_text(DOC_WITH_CLAIMS + "\nA new invariant must hold here.\n",
                   encoding="utf-8")

    second = mine_docs(sdk, str(root))
    assert second["new_points"] == 1
    # the OTHER doc is untouched and is skipped; the edited one is re-mined
    assert second["skipped_unchanged"] == 1
    assert second["mined"] == 1
    # the NEW claim is the one that was added, and nothing else was minted
    assert _graph(sdk).query(
        "MATCH (p:Point {content:'A new invariant must hold here'}) RETURN 1"
    ).result_set
    # the invariant the two mechanisms together must hold on every run:
    # every claim accounted for is either new or already present
    assert second["existing_points"] == second["claims"] - second["new_points"]
    assert len(_points(sdk)) == before + 1


def test_no_repo_and_no_about_entities_never_fabricates_an_object(corpus):
    """Honest absence: with no ``--repo``/``--about`` and a corpus that is not
    the staged ``<org>/<repo>`` shape, no Object is minted and no
    ``aboutObject`` edge exists."""
    sdk, _root, base = corpus
    loose = Path(base) / "loose-corpus"
    loose.mkdir()
    (loose / "d.md").write_text(DOC_SECOND, encoding="utf-8")

    report = mine_docs(sdk, str(loose), repo=None, about_entities=[])

    assert report["repo"] is None
    assert report["objects"] == 0
    assert report["new_points"] > 0
    assert not _graph(sdk).query("MATCH (o:Object) RETURN o LIMIT 1").result_set
    assert _graph(sdk).query(
        "MATCH ()-[:aboutObject]->() RETURN count(*)"
    ).result_set[0][0] == 0


@pytest.fixture
def ent_graph(tmp_path):
    """A graph of its OWN, per test, for the entity-resolution tests.

    They create Objects directly, so they must NOT ride a shared graph: on the
    URI lane a namespace maps to a VERBATIM server graph (no path hash), so a
    shared namespace is ONE graph for the whole session and a stray Object (or
    a shared name) is visible to every other test using it. A private
    NAMESPACE per test is the isolation, and every SDK opened here is closed on
    teardown.
    """
    from tortoise.sdk import TortoiseSDK

    opened = []

    def make(namespace: str):
        sdk = TortoiseSDK(str(tmp_path / f"{namespace}.db"),
                          namespace=namespace)
        opened.append(sdk)
        return sdk

    yield make
    for sdk in opened:
        sdk.close()


def test_entity_name_resolution_refuses_two_live_holders(ent_graph):
    """#3633 route-then-refuse, on the miner's NAME arm: two LIVE Objects
    sharing a name must REFUSE, not hand back the first one.

    `_ensure_entities` resolves a name through
    `entity_identity.resolve_entity_id`, whose multi-holder arm refuses. The
    raw `MATCH (o:Object {name:$name}) RETURN o.id` read it replaced took
    `rows[0]`, so a name held by two live Objects wired every claim to an
    arbitrary one — silently, and with no way for a reader to notice.

    MUTATION THAT REDS THIS: restore the raw name-keyed read.
    """
    from tortoise.docs_miner import _ensure_entities
    from tortoise.entity_identity import AmbiguousEntityName

    sdk = ent_graph("test_docs_two_live")
    proj = sdk._get_proj()
    for oid in ("obj-a", "obj-b"):
        proj.g.query("MERGE (o:Object {id:$id}) "
                     "SET o.name = $name, o.status = 'live'",
                     params={"id": oid, "name": "Acme"})

    class _NoMint:
        def add_object(self, *a, **k):  # pragma: no cover - must not be hit
            raise AssertionError("the refusal must precede the mint")

    with pytest.raises(AmbiguousEntityName):
        _ensure_entities(_NoMint(), proj, [{
            "id": "obj-new", "name": "Acme",
            "object_kind": REPO_OBJECT_KIND,
            "externalId": "github:repo:acme/acme"}])


def test_entity_name_resolution_returns_the_single_live_holder(ent_graph):
    """…and exactly ONE live holder is still resolved — the happy path the
    route must not break — without minting a second Object for that name."""
    from tortoise.docs_miner import _ensure_entities

    sdk = ent_graph("test_docs_one_live")
    proj = sdk._get_proj()
    proj.g.query("MERGE (o:Object {id:$id}) "
                 "SET o.name = $name, o.status = 'live'",
                 params={"id": "obj-only", "name": "Acme"})

    class _NoMint:
        def add_object(self, *a, **k):  # pragma: no cover - must not be hit
            raise AssertionError("a resolved name must not mint a second node")

    assert _ensure_entities(_NoMint(), proj, [{
        "id": "obj-new", "name": "Acme",
        "object_kind": REPO_OBJECT_KIND,
        "externalId": "github:repo:acme/acme"}]) == ["obj-only"]


def test_entity_name_resolution_refuses_a_terminal_only_name(ent_graph):
    """Minting is for a genuinely UNHELD name only.

    `resolve_entity_id` returns ``None`` for a name with NO holder AND for one
    whose holder(s) are all TERMINAL, so minting on the second would write the
    new id onto EVERY same-name holder (the projection's object write MERGEs
    ``(o:Object {name:$name})``): two terminal holders would both then claim
    one id — the ``duplicate-id`` corruption the resolver itself refuses — and
    a single terminal holder would be silently re-keyed and served as if live.
    A terminal-only reference is #3633 D2's refusal; the miner must refuse it
    (and `mine_docs` records that refusal in ``result["errors"]``) rather than
    corrupt or resurrect an identity.

    MUTATION THAT REDS THIS: mint whenever `resolve_entity_id` returns None,
    without reading the holder set back.
    """
    from tortoise.docs_miner import _ensure_entities

    sdk = ent_graph("test_docs_terminal")
    proj = sdk._get_proj()
    for oid in ("dead-1", "dead-2"):
        proj.g.query("MERGE (o:Object {id:$id}) "
                     "SET o.name = $name, o.status = 'superseded'",
                     params={"id": oid, "name": "Acme"})

    class _NoMint:
        def add_object(self, *a, **k):  # pragma: no cover - must not be hit
            raise AssertionError("a held name must not mint")

    with pytest.raises(ValueError, match="held only by terminal"):
        _ensure_entities(_NoMint(), proj, [{
            "id": "obj-new", "name": "Acme",
            "object_kind": REPO_OBJECT_KIND,
            "externalId": "github:repo:acme/acme"}])

    held = sorted(r[0] for r in proj.g.query(
        "MATCH (o:Object {name:$name}) RETURN o.id",
        params={"name": "Acme"}).result_set)
    assert held == ["dead-1", "dead-2"], \
        "no id may be stamped onto the terminal holders"


def test_cli_verb_runs_the_pipeline_and_prints_the_zero_new_line(corpus, capsys):
    """The CLI wiring: ``tortoise mine-docs`` is the #1724 target verb."""
    from tortoise.__main__ import _cmd_mine_docs

    sdk, root, _base = corpus
    db = sdk._db_path
    sdk.close()

    args = SimpleNamespace(dir=str(root), db=str(db), corpus_name=None,
                           repo=None, about=None, max_claims_per_doc=40,
                           no_index=False)
    assert _cmd_mine_docs(args) == 0
    out = capsys.readouterr().out
    assert "NEW Points:" in out
    assert "status=draft" in out
