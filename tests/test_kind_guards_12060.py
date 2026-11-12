"""#12060 — per-kind write guards: redirect a mis-filed write, never refuse it.

MEASURED on the hosted graphs (2026-11-11), before this file: `default` held
**872 Subjects, 870 of kind `other`, 453 named `issue:<repo>#<n>`** — GitHub
issue identifiers filed as Subjects, invisible to every work-item read and
permanent (#2489: `rebuild_all` does not repair an existing graph). `live-v4`
held one person under five Subject names, a document and an attestation filed as
`core:role`, and `daniel-ospina/agent-infra` filed as a `core:naturalPerson`.

The contract these tests pin — one test class per acceptance point of the issue
plus the safety properties the owner's comment on the issue added:

1. ``issue:<repo>#<n>`` as a Subject lands as a work item and carries the exact
   AX block; the same write with ``intent="as_declared"`` lands as a Subject AND
   records the override.
2. A guard whose target pack is absent **abstains** — the write is never
   refused, and ``kind_redirect_skipped`` says why (the ladder has two steps:
   degrade to the core fallback, else leave the write unchanged).
3. ``tortoise pack validate`` refuses a non-``depends_on`` target and a
   fallback-less guard.
4. **Zero model calls** on every guard path.
5. Counters for ``(rule, from → to, accepted | overridden | skipped)``.
6. ``packs/_template`` carries a working example; a pack without ``guards:``
   behaves exactly as today.

Plus, because they are the properties that make the feature safe rather than
merely present:

* the kind↔layer answer is DERIVED from ``kind_layers`` — the guard table must
  not become a second authority for it (recorded decision, #9964/#9675);
* no shipped guard can produce a REFUSAL (#1934 warn-not-block, and the issue's
  own defining rule: a guard never refuses a case whose home is known);
* an explicit kind the caller declared is never overruled by a name guess;
* the guard runs AFTER the entity judgement, i.e. on the entity write door —
  and the role guard ABSTAINS until `holdsRole` exists (the owner's #12060
  comment: five orphan roles are worse than one person with five aliases).

Runnable embedded:
  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_kind_guards_12060.py -q
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise import kind_guards as kg
from tortoise.kind_guards import (
    CORE_CONFUSIONS,
    INTENT_AS_DECLARED,
    LINK_PREDICATES,
    Confusion,
    GuardOutcome,
    guard_table,
    guard_table_problems,
    kind_guard_counts,
    resolve,
)
from tortoise.sdk import TortoiseSDK

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "packs" / "_template" / "manifest.yaml"
TEST_GRAPH = "tortoise_test_12060_kind_guards"

ISSUE_NAME = "issue:daniel-ospina/tortoise#12060"


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_counts():
    kg._reset_kind_guard_counts()
    yield
    kg._reset_kind_guard_counts()


@pytest.fixture
def sdk(tmp_path):
    """A private embedded graph. The guard path needs no pack install."""
    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(str(tmp_path / "guards.db"), namespace=TEST_GRAPH,
                    event_log_path=str(events / "events.jsonl"))
    yield s
    with contextlib.suppress(Exception):
        s._get_proj().g.query("MATCH (n) DETACH DELETE n")
    s.close()


@pytest.fixture
def no_pack_guards(monkeypatch):
    """Isolate the CORE table from whatever the repo catalog declares.

    `resolve(pack_confusions=None)` reads the loaded pack catalog, which is the
    right production behaviour and the wrong test fixture: a pack in `packs/`
    that later declares a guard would change these assertions. Every core test
    therefore pins the table to "no pack guards".
    """
    monkeypatch.setattr(kg, "_catalog_pack_guards", lambda: ())


def _labels_kinds(sdk, node_id):
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.id=$i RETURN labels(n), n.objectKind, n.subjectKind",
        params={"i": node_id}).result_set
    assert rows, f"no node {node_id!r}"
    return list(rows[0])


def _node_props(sdk, node_id):
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.id=$i RETURN properties(n)",
        params={"i": node_id}).result_set
    assert rows, f"no node {node_id!r}"
    return dict(rows[0][0])


#: A name shape only a PACK guard declares — the core table is consulted first
#: (it carries the common cases and needs no graph read), so a ladder test that
#: reused `issue:` would be answered by the core rule and never reach the pack.
PACK_ONLY_NAME = "dev-issue-7"


def _pack_guard(**overrides) -> Confusion:
    """A pack-shaped guard for the ladder tests."""
    base = dict(
        rule="dev:issue-identifier",
        when="nameMatches",
        pattern=r"^dev-issue-[0-9]+$",
        redirect="dev:issue",
        fallback="core:WorkItem",
        reason="issue:<repo>#<n> is the dev pack's work-item kind",
        declaring_pack="dev",
    )
    base.update(overrides)
    return Confusion(**base)


# ══ 1. the issue's headline case: redirect + the AX block ═════════════════

class TestAcceptance1RedirectAndOverride:
    def test_issue_identifier_as_a_subject_lands_as_a_work_item(self, sdk,
                                                                no_pack_guards):
        r = sdk.create_subject(ISSUE_NAME)
        labels, object_kind, _subject_kind = _labels_kinds(sdk, r["id"])
        assert labels == ["Object"], (
            "an issue identifier minted a :Subject — the work-item read can "
            "never see it (#12060)")
        assert object_kind == "core:WorkItem"
        assert "kind_redirect" in r, r

    def test_the_ax_block_has_the_fixed_shape(self, sdk, no_pack_guards):
        r = sdk.create_subject(ISSUE_NAME)
        block = r["kind_redirect"]
        # what changed, why (rule id), the exact override, how to undo — as DATA.
        assert block["from"] == {"kind": None, "layer": "subject"}
        assert block["to"] == {"kind": "core:WorkItem", "layer": "object"}
        assert block["rule"] == "issue-identifier-is-work-item"
        assert block["override"] == 'intent="as_declared"'
        assert isinstance(block["undo"], str) and block["undo"]
        assert block["reason"]
        assert block["degraded"] is False
        # …and the human render carries the same four facts.
        msg = block["message"]
        assert "redirected: Subject → Object (core:WorkItem)" in msg
        assert "issue-identifier-is-work-item" in msg
        assert 'intent="as_declared"' in msg
        assert "undo:" in msg

    def test_as_declared_lands_as_a_subject_and_records_the_override(
            self, sdk, no_pack_guards):
        r = sdk.create_subject(ISSUE_NAME, intent=INTENT_AS_DECLARED)
        labels, _ok, _sk = _labels_kinds(sdk, r["id"])
        assert labels == ["Subject"], "the caller's intent was overruled"
        assert "kind_redirect" not in r
        assert r["kind_guard_overridden"]["rule"] == "issue-identifier-is-work-item"
        assert r["kind_guard_overridden"]["target"] == "core:WorkItem"
        assert r["kind_guard_overridden"]["declaredLayer"] == "subject"

    def test_the_override_is_counted(self, sdk, no_pack_guards):
        sdk.create_subject(ISSUE_NAME, intent=INTENT_AS_DECLARED)
        rows = kind_guard_counts()
        assert [x["outcome"] for x in rows] == ["overridden"]
        assert rows[0]["rule"] == "issue-identifier-is-work-item"

    def test_the_override_is_reachable_through_the_props_passthrough(
            self, sdk, no_pack_guards):
        """The AX block tells the caller to pass `intent="as_declared"` — so the
        remedy must be reachable from EVERY door, or it is advice nobody can
        take. The MCP tools forward their `props` dict straight into the SDK
        door (`mcp_server.tortoise_create_subject` does
        `create_subject(name, subjectKind, **(props or {}))`), and `intent` is a
        declared keyword there, so the override travels that channel with NO
        tool-parameter change (the #12060 out-of-scope rule: no MCP surface
        change without the owner)."""
        r = sdk.create_subject(ISSUE_NAME, **{"intent": INTENT_AS_DECLARED})
        assert _labels_kinds(sdk, r["id"])[0] == ["Subject"]
        assert r["kind_guard_overridden"]["rule"] == "issue-identifier-is-work-item"

    def test_a_non_token_intent_stays_a_data_property(self, sdk, no_pack_guards):
        """⛔ Only the EXACT token is a control. `intent` is a plausible data key
        and this door is reached by `**(props or {})` from the MCP tools, so
        reserving the name unconditionally would silently DROP a legitimate
        property — and a reserved-name refusal would be a refusal on a path that
        must never refuse (#1934)."""
        r = sdk.create_subject("Widget", intent="purchase")
        assert "kind_guard_overridden" not in r
        assert _node_props(sdk, r["id"])["intent"] == "purchase"
        # …and the guard still fires, because a non-token is not an override
        r2 = sdk.create_subject(ISSUE_NAME, intent="purchase")
        assert r2["kind_redirect"]["rule"] == "issue-identifier-is-work-item"

    def test_the_alias_doors_surface_the_ax_fields(self, sdk, no_pack_guards):
        """The alias unwrapped ``result["node"]`` and dropped top-level keys —
        the #8171 P2 the advisories already got. The AX fields ride the node."""
        r = sdk.create_subject(ISSUE_NAME)
        assert r["kind_redirect"]["rule"] == "issue-identifier-is-work-item"
        r2 = sdk.create_object("issue:daniel-ospina/tortoise#99999")
        assert r2["objectKind"] == "core:WorkItem"
        assert r2["kind_redirect"]["to"]["kind"] == "core:WorkItem"

    def test_declared_kind_and_layer_are_preserved_on_the_node(
            self, sdk, no_pack_guards):
        """R5 — an override, a replay or an audit can restore what was asked."""
        r = sdk.create_subject("daniel-ospina/agent-infra",
                               subjectKind="core:naturalPerson")
        assert _labels_kinds(sdk, r["id"])[1] == "core:Project"
        props = _node_props(sdk, r["id"])
        assert props["declaredKind"] == "core:naturalPerson"
        assert props["declaredLayer"] == "subject"
        # …and WHICH RULE moved it, on the node: a redirect that lived only in
        # one response would be a silent mutation nobody could attribute after
        # the process that minted it is gone (the counters are in-process).
        assert props["kindRedirectRule"] == "repo-slug-is-not-a-person"

    def test_the_guard_runs_before_the_kind_layer_router(self, sdk,
                                                         no_pack_guards):
        """ONE decision, taken once: after the guard rewrites the kind, the
        kind↔layer router sees a consistent write and stays silent."""
        r = sdk.create_subject(ISSUE_NAME)
        codes = [a.get("code") for a in (r.get("advisories") or [])]
        assert "kind_layer_routed" not in codes, r.get("advisories")
        assert "kind_other_layer" not in codes


# ══ 2. a missing target ABSTAINS — nothing is ever refused ════════════════

class TestAcceptance2Abstention:
    def test_degrade_to_the_core_fallback_when_the_target_is_absent(self):
        """R4 ladder step 1: a slightly less specific home beats none.

        The shape: `acme` IS installed and its guard fires, but the kind it
        redirects to lives in `dev`, which this graph does not have — so the
        guard degrades to its declared core fallback instead of doing nothing.
        (A guard whose target were in its OWN namespace could never hit this
        branch: the target is installed exactly when the declarer is.)
        """
        d = resolve(PACK_ONLY_NAME, "subject", None,
                    pack_confusions=(_pack_guard(declaring_pack="acme"),),
                    installed=frozenset({"core", "acme"}))
        assert d.outcome == GuardOutcome.REDIRECT
        assert d.kind == "core:WorkItem", "the declared fallback was not used"
        assert d.redirect["degraded"] is True
        assert d.redirect["to"]["kind"] == "core:WorkItem"
        # …and the target it could NOT use is reported, not hidden.
        assert d.skipped == {"rule": "dev:issue-identifier", "target": "dev:issue",
                             "reason": kg.SKIP_NAMESPACE_NOT_INSTALLED}

    def test_no_usable_home_leaves_the_write_unchanged(self):
        """R4 ladder step 2: nothing is refused, and nothing is rewritten."""
        guard = _pack_guard(fallback="", declaring_pack="acme")
        d = resolve(PACK_ONLY_NAME, "subject", None,
                    pack_confusions=(guard,),
                    installed=frozenset({"core", "acme"}))
        assert d.outcome == GuardOutcome.SKIPPED
        assert d.kind is None and d.prop is None, "the write was changed"
        assert d.skipped["reason"] == kg.SKIP_NAMESPACE_NOT_INSTALLED

    def test_a_pack_guard_whose_declaring_pack_is_not_installed_abstains(self):
        # a guard whose OWN pack is missing must not fire at all — even when
        # its redirect target would have been available
        d2 = resolve(PACK_ONLY_NAME, "subject", None,
                     pack_confusions=(_pack_guard(redirect="core:WorkItem",
                                                  fallback="core:WorkItem"),),
                     installed=frozenset({"core"}))
        assert d2.outcome == GuardOutcome.SKIPPED
        assert d2.skipped["reason"] == kg.SKIP_DECLARING_PACK_NOT_INSTALLED
        assert d2.kind is None

    def test_a_pack_guard_fires_when_its_pack_is_installed(self):
        d = resolve(PACK_ONLY_NAME, "subject", None,
                    pack_confusions=(_pack_guard(),),
                    installed=frozenset({"core", "dev"}))
        assert d.outcome == GuardOutcome.REDIRECT
        assert d.kind == "dev:issue"
        assert d.redirect["degraded"] is False
        assert d.skipped is None

    def test_an_unreadable_installed_set_abstains_instead_of_failing(self):
        def boom():
            raise RuntimeError("graph unreachable")

        d = resolve(PACK_ONLY_NAME, "subject", None,
                    pack_confusions=(_pack_guard(),), installed_provider=boom)
        assert d.outcome == GuardOutcome.SKIPPED
        assert d.skipped["reason"] == kg.SKIP_INSTALLED_SET_UNREADABLE

    def test_no_branch_ever_refuses(self, sdk, no_pack_guards, monkeypatch):
        """The write path is TOTAL: every abstention still lands the write."""
        # The SDK's installed-set reader is the one seam the ladder consults.
        monkeypatch.setattr("tortoise.sdk._graph_installed_namespaces",
                            lambda _sdk: frozenset({"core"}))
        # Every one of these exercises a different branch: a redirect (issue:),
        # an abstention with no link predicate ("the owner", skipped BEFORE the
        # installed-set check), a redirect from a declared person kind, and the
        # silent path. NONE of them may raise, and every one must LAND.
        for name, kind, expected_label in (
                (ISSUE_NAME, None, "Object"),
                ("the owner", None, "Subject"),          # abstains, unchanged
                ("daniel-ospina/agent-infra", "core:naturalPerson", "Object"),
                ("Acme Corp", None, "Subject")):
            r = sdk.create_subject(name, subjectKind=kind)
            assert r.get("id"), name
            assert _labels_kinds(sdk, r["id"])[0] == [expected_label], name
        # The abstention was reported, not swallowed...
        assert resolve("the owner", "subject", None,
                       pack_confusions=()).skipped["reason"].startswith(
                           "link-predicate-unimplemented")
        # ...and the outcomes were counted.
        assert {r["outcome"] for r in kind_guard_counts()} == {"accepted", "skipped"}


class TestNeverRefuses:
    """A guard must never be the reason a write fails — the invariant, on its own."""

    def test_the_guard_path_never_raises_whatever_the_input(self, no_pack_guards):
        weird = (None, 1, "", " ", "issue:", "issue:a#", "issue:a#x",
                 "issue:" + "a" * 5000 + "#1", "the", "the owner ", "a/b/c",
                 "\x00", "issue:ä#1", "THE OWNER")
        for name in weird:
            for layer in ("subject", "object", "point", "event", "", "nonsense"):
                for kind in (None, "", "other", "core:team", "x:y", 3):
                    for intent in (None, "as_declared", "AS_DECLARED", 1, ""):
                        d = resolve(name, layer, kind, intent=intent,
                                    pack_confusions=())
                        assert d.outcome in GuardOutcome.ALL
                        if d.kind is not None:
                            assert d.outcome == GuardOutcome.REDIRECT

    def test_a_malformed_pack_guard_cannot_break_a_write(self):
        """A broken install is a broken INSTALL (#1934) — the guard just cannot
        fire, and the write is unaffected."""
        bad = [
            {"kind": "concept", "onConfusion": "not-a-list"},
            {"kind": "concept", "onConfusion": [{"when": "nameMatches"}]},
            {"kind": "concept", "onConfusion": [
                {"when": "nonsense", "pattern": "x", "redirect": "concept"}]},
            {"kind": "concept", "onConfusion": [
                {"when": "nameMatches", "pattern": "^(unclosed",
                 "redirect": "concept", "fallback": "core:WorkItem"}]},
            "not-a-map",
        ]
        # Every entry that is not a usable guard is dropped…
        assert kg.pack_guards("acme", bad[:3] + bad[4:]) == ()
        # …and one whose PATTERN does not compile survives the compile step (so
        # `guard_table_problems` can REPORT it) but can never fire, so the write
        # is untouched.
        compiled = kg.pack_guards("acme", [bad[3]])
        assert len(compiled) == 1
        d = resolve("acme-1", "subject", None, pack_confusions=compiled)
        assert d.outcome == GuardOutcome.ACCEPT

    def test_the_role_guard_abstains_until_holdsRole_is_implemented(self):
        """The owner's #12060 comment: five ORPHAN roles are worse than one
        person with five aliases, so a redirect TO a kind whose linking
        predicate is unbuilt abstains."""
        assert LINK_PREDICATES["holdsRole"] is False  # #12047, not a code edit
        d = resolve("the owner", "subject", None, pack_confusions=())
        assert d.outcome == GuardOutcome.SKIPPED
        assert d.kind is None
        assert d.skipped["reason"] == "link-predicate-unimplemented:holdsRole"
        assert d.skipped["target"] == "core:role"


# ══ 3. the INSTALL gate ═══════════════════════════════════════════════════

def _run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tortoise", *argv],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=180)


def _write_pack(root: Path, namespace: str, *, object_kinds: str = "concept",
                guards: str, depends_on: str = "[]") -> Path:
    """A minimal, schema-valid pack whose ``ontology.guards`` is as given."""
    p = root / namespace
    p.mkdir(parents=True, exist_ok=True)
    (p / "manifest.yaml").write_text(
        f"""namespace: {namespace}
name: {namespace.title()} Test Pack
version: "0.1.0"
tier: free
ontology:
  extends: core
  objectKinds: [{object_kinds}]
  guards:
{guards}
depends_on: {depends_on}
""")
    return p / "manifest.yaml"


GOOD_GUARD = """\
  - kind: concept
    layer: object
    onConfusion:
      - when: nameMatches
        pattern: '^issue:[^#\\s]+#[0-9]+$'
        redirect: core:WorkItem
        fallback: core:WorkItem
        reason: 'an issue identifier is a work item'
"""


class TestAcceptance3InstallGate:
    def test_a_legal_guard_validates_clean(self, tmp_path):
        manifest = _write_pack(tmp_path / "packs", "acme", guards=GOOD_GUARD)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_refuses_a_target_namespace_not_in_depends_on(self, tmp_path):
        """R3a — target closure: core, own namespace, or a declared dependency."""
        bad = """\
  - kind: concept
    layer: object
    onConfusion:
      - when: nameMatches
        pattern: '^issue:[^#\\s]+#[0-9]+$'
        redirect: dev:issue
        fallback: core:WorkItem
        reason: 'not declared as a dependency'
"""
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "depends_on" in out and "dev" in out

    def test_the_same_target_is_legal_once_depends_on_declares_it(self, tmp_path):
        ok = GOOD_GUARD.replace("redirect: core:WorkItem", "redirect: dev:issue")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=ok,
                               depends_on="[dev]")
        r = _run_cli("pack", "validate", str(manifest.parent))
        out = r.stdout + r.stderr
        # `dev` is not in the temp catalog, so the pack is *loaded but
        # unresolved* — which `pack validate` already refuses (#9663). What this
        # pins is that the GUARD half no longer contributes an error.
        assert "neither owns nor declares" not in out
        assert "depends_on: declared dependency 'dev' is not loaded" in out

    def test_refuses_a_guard_without_a_core_fallback(self, tmp_path):
        """R3b — the fallback is the one home that always exists."""
        bad = GOOD_GUARD.replace("        fallback: core:WorkItem\n", "")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "fallback: required" in out

    def test_refuses_a_fallback_that_is_not_core(self, tmp_path):
        bad = GOOD_GUARD.replace("fallback: core:WorkItem", "fallback: acme:concept")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "is not a core kind" in out

    def test_refuses_a_guard_on_a_kind_the_pack_does_not_declare(self, tmp_path):
        bad = GOOD_GUARD.replace("kind: concept", "kind: notmine")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 1
        assert "neither a core kind nor a kind this pack declares" in (
            r.stdout + r.stderr)

    def test_refuses_a_guard_on_the_wrong_layer(self, tmp_path):
        bad = GOOD_GUARD.replace("layer: object", "layer: event")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 1
        assert "is declared on the object layer" in (r.stdout + r.stderr)

    def test_refuses_a_malformed_pattern(self, tmp_path):
        bad = GOOD_GUARD.replace(r"pattern: '^issue:[^#\s]+#[0-9]+$'",
                                 "pattern: '^(unclosed'")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 1
        assert "does not compile" in (r.stdout + r.stderr)

    def test_refuses_a_duplicate_rule_id(self, tmp_path):
        """A rule id is what the counters key on, so two guards sharing one is a
        measurement that lies."""
        one = GOOD_GUARD.replace("      - when: nameMatches",
                                 "      - rule: dup\n        when: nameMatches")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=one + one)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 1
        assert "duplicate rule id" in (r.stdout + r.stderr)


# ══ 4. zero model calls ═══════════════════════════════════════════════════

class TestAcceptance4NoModelCalls:
    def test_the_guard_path_issues_zero_model_calls(self, sdk, no_pack_guards,
                                                    monkeypatch):
        """A guard that needs a model is a different feature — it must not be
        smuggled into this one. Every model/embedding seam raises."""

        def _no_model(*_a, **_kw):
            raise AssertionError("a model call was made on the guard path")

        import tortoise.embeddings as embeddings_mod
        import tortoise.extractor_v2 as ext

        for mod, name in ((embeddings_mod, "EmbeddingModel"),
                          (ext, "_call_llm"), (ext, "llm_extract")):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, _no_model, raising=False)
        monkeypatch.setattr(kg, "_catalog_pack_guards", lambda: ())

        # the pure resolver, on every outcome shape
        for name, layer, kind, intent in (
                (ISSUE_NAME, "subject", None, None),
                (ISSUE_NAME, "subject", None, INTENT_AS_DECLARED),
                ("the owner", "subject", None, None),
                ("daniel-ospina/agent-infra", "subject", "core:naturalPerson", None),
                ("Acme Corp", "object", None, None)):
            resolve(name, layer, kind, intent=intent, pack_confusions=())
        # …and a full write through the door
        r = sdk.create_subject(ISSUE_NAME)
        assert r["kind_redirect"]["rule"] == "issue-identifier-is-work-item"

    def test_the_guard_module_imports_nothing_at_module_scope(self):
        """Stdlib-only at module scope, like `kind_fallbacks`/`kind_layers` —
        the write doors must not inherit a cycle (or a model SDK) through it."""
        import ast

        tree = ast.parse(Path(kg.__file__).read_text())
        top = [n for n in tree.body
               if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = set()
        for node in top:
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        assert names <= {"__future__", "collections", "contextlib", "logging",
                         "re", "threading", "dataclasses", "typing"}, names


# ══ 5. measurement ════════════════════════════════════════════════════════

class TestAcceptance5Counters:
    def test_the_three_outcomes_are_counted(self):
        resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        resolve(ISSUE_NAME, "subject", None, intent=INTENT_AS_DECLARED,
                pack_confusions=())
        resolve("the owner", "subject", None, pack_confusions=())
        rows = kind_guard_counts()
        assert {r["outcome"] for r in rows} == set(kg.COUNT_OUTCOMES)
        assert {r["rule"] for r in rows} == {
            "issue-identifier-is-work-item",
            "standing-noun-is-a-role-not-a-person"}
        for row in rows:
            assert set(row) == {"rule", "from", "to", "outcome", "count"}
            assert row["count"] == 1

    def test_the_pair_is_what_is_counted(self):
        """`from → to` is part of the key: a guard whose redirect target moves
        must not be summed with its old target."""
        resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        resolve("daniel-ospina/agent-infra", "subject", "core:naturalPerson",
                pack_confusions=())
        rows = {r["rule"]: r for r in kind_guard_counts()}
        assert rows["issue-identifier-is-work-item"]["from"] == "<unnamed>"
        assert rows["issue-identifier-is-work-item"]["to"] == "core:WorkItem"
        assert rows["repo-slug-is-not-a-person"]["from"] == "core:naturalPerson"
        assert rows["repo-slug-is-not-a-person"]["to"] == "core:Project"

    def test_the_label_set_is_bounded(self, monkeypatch):
        """`from` is CALLER INPUT — an unbounded label set is a memory leak with
        a metrics interface (the rule `monitoring._admit_bounded_label` states)."""
        monkeypatch.setattr(kg, "COUNT_LABEL_CAP", 8)
        kg._reset_kind_guard_counts()
        for i in range(40):
            kg._record("issue-identifier-is-work-item", f"customKind{i}",
                       "core:WorkItem", GuardOutcome.REDIRECT)
        labels = {r["from"] for r in kind_guard_counts()}
        assert len(labels) <= 9  # the cap, plus the <other> fold
        assert "<other>" in labels

    def test_a_silent_write_is_not_counted(self):
        resolve("Acme Corp", "object", None, pack_confusions=())
        assert kind_guard_counts() == []

    def test_monitoring_reads_the_one_tally(self):
        from tortoise import monitoring

        resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        assert monitoring.kind_guard_counts() == kind_guard_counts()


# ══ 6. the pack extension point ═══════════════════════════════════════════

def _catalog_pack(tmp_path, monkeypatch, namespace="acme", *, guards=None,
                  depends_on="[]", object_kinds="concept"):
    """Write a pack into a private catalog and point BOTH readers at it.

    `TORTOISE_PACKS_DIR` (not `domain_loader._PACKS_DIR`) is the seam here on
    purpose: the pack REGISTRY (which carries `guards`) and the kind-INDEX spec
    (which decides a pack kind's layer) both resolve through
    `pack_registry.default_packs_dir()`, so an env-only catalog is the one shape
    where a test exercises the real resolution instead of half of it.
    """
    from tortoise import domain_loader
    from tortoise.value_extractor import _clear_kind_spec_cache

    root = tmp_path / "catalog"
    if guards is None:
        guards = GOOD_GUARD
    _write_pack(root, namespace, object_kinds=object_kinds, guards=guards,
                depends_on=depends_on)
    monkeypatch.setenv("TORTOISE_PACKS_DIR", str(root))
    domain_loader._PACKS_DIR = None
    domain_loader._registry = None
    _clear_kind_spec_cache()
    return root


class TestAcceptance6PackExtensionPoint:
    def test_the_template_carries_a_working_example(self):
        """Required in `_template/` — every new pack starts with the shape.

        The template is a SKELETON (`namespace:` is blank on purpose), so it
        cannot load as a pack; its guard block is asserted as YAML plus a
        compile through the same reader the write path uses.
        """
        import yaml

        text = TEMPLATE.read_text()
        assert "guards:" in text and "onConfusion:" in text
        assert "fallback: core:WorkItem" in text
        raw = yaml.safe_load(text)
        assert raw["ontology"]["guards"] == [], "the shipped value is 'no guards'"
        # …and the commented example is a guard this module accepts.
        example = [{
            "kind": "concept",
            "layer": "object",
            "onConfusion": [{
                "when": "nameMatches",
                "pattern": r"^issue:[^#\s]+#[0-9]+$",
                "redirect": "concept",
                "fallback": "core:WorkItem",
                "reason": "issue:<repo>#<n> names a tracked work item",
            }],
        }]
        compiled = kg.pack_guards("acme", example)
        assert len(compiled) == 1
        assert compiled[0].redirect == "acme:concept"
        assert compiled[0].fallback == "core:WorkItem"

    def test_a_pack_without_guards_behaves_exactly_as_today(self, tmp_path, sdk,
                                                            monkeypatch):
        """Absent ``guards:`` means "no redirects" — not "no write"."""
        _catalog_pack(tmp_path, monkeypatch, "acme", guards=" []")
        assert kg._catalog_pack_guards() == ()
        r = sdk.create_subject("acme widget")
        assert "kind_redirect" not in r and "kind_redirect_skipped" not in r

    def test_a_pack_guard_is_read_from_the_catalog_and_fires(self, tmp_path, sdk,
                                                             monkeypatch):
        _catalog_pack(tmp_path, monkeypatch, "acme",
                      guards="""
  - kind: concept
    layer: object
    onConfusion:
      - when: nameMatches
        pattern: '^acme-[0-9]+$'
        redirect: acme:concept
        fallback: core:WorkItem
        reason: 'an acme-<n> handle is this pack''s concept kind'
""")
        rules = {c.rule for c in kg._catalog_pack_guards()}
        assert any(r.startswith("acme:") for r in rules), rules
        r = sdk.create_subject("acme-42")
        assert r["kind_redirect"]["to"]["kind"] == "acme:concept"
        assert _labels_kinds(sdk, r["id"])[1] == "acme:concept"

    def test_a_pack_guard_naming_an_absent_pack_abstains_end_to_end(
            self, tmp_path, sdk, monkeypatch):
        _catalog_pack(tmp_path, monkeypatch, "acme",
                      guards="""
  - kind: concept
    layer: object
    onConfusion:
      - when: nameMatches
        pattern: '^ISSUE-[0-9]+$'
        redirect: core:WorkItem
        fallback: core:WorkItem
        reason: 'an ISSUE-<n> handle is a work item'
""")
        # the graph HAS pack records, and this one is not among them
        monkeypatch.setattr("tortoise.sdk._graph_installed_namespaces",
                            lambda _sdk: frozenset({"core"}))
        r = sdk.create_subject("ISSUE-7")
        assert r.get("id")
        assert r["kind_redirect_skipped"]["reason"] == \
            kg.SKIP_DECLARING_PACK_NOT_INSTALLED
        assert _labels_kinds(sdk, r["id"])[0] == ["Subject"], "the write changed"

    def test_the_guard_table_reports_its_own_incoherence(self):
        assert guard_table_problems() == []


# ══ the safety properties ═════════════════════════════════════════════════

class TestSafetyProperties:
    def test_the_layer_is_derived_from_kind_layers_not_redeclared(self):
        """ONE authority for kind↔layer (#9964/#9675) — the guard table must not
        become a second answer to it."""
        from tortoise.kind_layers import routing_layers

        for kind, entry in guard_table(include_packs=False).items():
            assert entry["allowedLayers"] == sorted(routing_layers(kind)), kind
            assert entry["layer"] == next(iter(sorted(routing_layers(kind))), None)

    def test_every_core_kind_has_a_guard_table_entry(self):
        from tortoise.kind_layers import core_sections

        table = guard_table(include_packs=False)
        assert set(core_sections()) <= set(table)

    def test_no_shipped_guard_can_refuse(self):
        """#1934 warn-not-block + the issue's own rule: a refusal has no
        producer, because every guard carries a core fallback (R3)."""
        for c in CORE_CONFUSIONS:
            assert c.fallback, c.rule
        assert GuardOutcome.REFUSE not in {
            d.outcome for d in (
                resolve(ISSUE_NAME, "subject", None, pack_confusions=()),
                resolve(ISSUE_NAME, "subject", None, intent=INTENT_AS_DECLARED,
                        pack_confusions=()),
                resolve("the owner", "subject", None, pack_confusions=()),
            )}

    def test_an_explicit_kind_is_never_overruled_by_a_name_guess(self):
        d = resolve(ISSUE_NAME, "subject", "core:team", pack_confusions=())
        assert d.outcome == GuardOutcome.ACCEPT
        assert d.kind is None

    def test_a_nil_kind_does_not_block_the_guard(self):
        """`other` asserts nothing about the layer, so it is not a declaration
        the guard has to respect (the #9964 review's NIL rule)."""
        for nil in ("other", "core:other"):
            d = resolve(ISSUE_NAME, "subject", nil, pack_confusions=())
            assert d.outcome == GuardOutcome.REDIRECT, nil

    def test_a_write_already_filed_right_is_left_alone(self):
        d = resolve(ISSUE_NAME, "object", "core:WorkItem", pack_confusions=())
        assert d.outcome == GuardOutcome.ACCEPT

    def test_resolution_is_deterministic(self):
        a = resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        b = resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        assert a == b

    def test_only_entity_writes_are_guarded(self):
        for layer in ("point", "event", "document"):
            d = resolve(ISSUE_NAME, layer, None, pack_confusions=())
            assert d.outcome == GuardOutcome.ACCEPT, layer

    def test_the_issue_pattern_does_not_match_a_bare_repo_and_pr(self):
        """The pattern is anchored, so a PR title quoting an issue id is not a
        work item by itself."""
        for name in ("issue:foo", "issue:#1", "the issue:foo/bar#1 above",
                     "issue:foo/bar#x"):
            d = resolve(name, "subject", None, pack_confusions=())
            assert d.outcome == GuardOutcome.ACCEPT, name
