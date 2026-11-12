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
import gc
import json
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
                           "link-predicate-unavailable")
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

    def test_the_role_guard_abstains_until_a_redirect_can_name_a_holder(self):
        """The owner's #12060 comment: five ORPHAN roles are worse than one
        person with five aliases, so a redirect TO a kind that needs a linking
        predicate abstains.

        ⚠️ The reason is NOT "unimplemented" any more. `#12047` LANDED
        (`entity_resolver.wire_holds_role`) and the edge is produced whenever a
        caller names a `roleHolder`; what a NAME-MATCHED redirect cannot do is
        name a holder. Asserting the old token here would pin a false statement
        about the tree into the disclosure a client branches on.
        """
        assert LINK_PREDICATES["holdsRole"] is False  # a redirect has no holder
        d = resolve("the owner", "subject", None, pack_confusions=())
        assert d.outcome == GuardOutcome.SKIPPED
        assert d.kind is None
        assert d.skipped["reason"] == "link-predicate-unavailable:holdsRole"
        assert d.skipped["target"] == "core:role"


# ══ 3. the INSTALL gate ═══════════════════════════════════════════════════

def _template_guard_example() -> dict:
    """The ``guards:`` worked example, parsed OUT of the template's comment block.

    The template must ship ``guards: []`` (a scaffolded pack behaves exactly as
    today), so the example lives in comments — and a comment is exactly what rots
    unverified. This de-comments the block and hands back real data, so the tests
    below can run it through the same validator a pack author runs.
    """
    import yaml

    lines = TEMPLATE.read_text().splitlines()
    block: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if not inside:
            # the EXAMPLE block starts at a comment whose content is exactly
            # `guards:` — the prose above it also mentions the key.
            if stripped.startswith("#") and stripped.lstrip("#").strip() == "guards:":
                inside = True
                block.append("guards:")
            continue
        if not stripped.startswith("#"):
            break
        body = stripped[1:]
        if not body.strip():
            break
        block.append(body[2:] if body.startswith("  ") else body.lstrip())
    assert block, "the template lost its worked example"
    parsed = yaml.safe_load("\n".join(block))
    assert isinstance(parsed, dict) and len(parsed["guards"]) == 1, parsed
    return parsed["guards"][0]


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
        smuggled into this one. Every model/embedding seam raises.

        ⛔ The seams are ASSERTED PRESENT before they are patched. A first draft
        patched `extractor_v2._call_llm` and `extractor_v2.llm_extract`, neither
        of which exists — so two thirds of this test silently did nothing and a
        model call added through any real seam would have been invisible.
        """
        import tortoise.embeddings as embeddings_mod
        import tortoise.extractor_v2 as ext
        import tortoise.kind_classifier as kc

        def _no_model(*_a, **_kw):
            raise AssertionError("a model call was made on the guard path")

        seams = [
            (embeddings_mod.EmbeddingModel, "get"),   # the singleton loader
            (ext, "_complete"),                       # every LLM call in the extractor
            (kc.KindClassifier, "classify_items"),    # the kind classifier's entry
        ]
        for owner, name in seams:
            assert hasattr(owner, name), (
                f"seam {getattr(owner, '__name__', owner)}.{name} disappeared — "
                f"this test would stop protecting anything")
            monkeypatch.setattr(owner, name, _no_model, raising=False)
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
                         "re", "threading", "weakref", "dataclasses",
                         "typing"}, names


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

        # …and the COMMENTED worked example is parsed OUT OF THE TEMPLATE and
        # run through the real install gate. A test that transcoded the example
        # into a Python literal would pass forever while the shipped comment
        # rotted — the example is the artifact pack authors copy, so the example
        # is what must be verified.
        example = _template_guard_example()
        assert example["kind"] == "concept" and example["layer"] == "object"
        compiled = kg.pack_guards("acme", [example])
        assert len(compiled) == 1
        assert compiled[0].redirect == "acme:concept"
        assert compiled[0].fallback == "core:WorkItem"
        assert compiled[0].guarded_kind == "acme:concept"

    def test_the_templates_worked_example_passes_pack_validate(self, tmp_path):
        """The shipped example must survive `tortoise pack validate` — the gate a
        pack author will actually run on their copy of it."""
        import yaml

        manifest = tmp_path / "packs" / "acme" / "manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(yaml.safe_dump({
            "namespace": "acme",
            "name": "Acme",
            "version": "0.1.0",
            "tier": "free",
            "ontology": {
                "extends": "core",
                "objectKinds": ["concept"],
                "guards": [_template_guard_example()],
            },
            "depends_on": [],
        }, sort_keys=False))
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 0, r.stdout + r.stderr

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
        # The table's own coherence check has a RUNNER here, where the pack is in
        # the resolved catalog: every redirect routable by `kind_layers`, every
        # fallback a usable core kind. (`guard_table_problems` cannot verify a
        # pack kind whose pack is absent from the catalog — it reports it as
        # unroutable, which is honest but is not this test's subject.)
        assert kg.guard_table_problems() == []

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


# ══ the review round: defects found and closed ════════════════════════════

class TestReviewFindingsClosed:
    """Each of these failed before the fix it names — the reproduction is the test."""

    def test_a_props_bag_carrying_intent_does_not_collide_at_the_relay(
            self, sdk, no_pack_guards):
        """#12060 review P1. `props` is the caller's bag after `_coerce_props`
        (the documented `props={"k": v}` convention), so `intent=intent, **props`
        raised `TypeError: got multiple values for keyword argument 'intent'` AT
        THE RELAY — the #2296 relay-collision class, reopened by adding a
        parameter and not a strip. The bag is now built, not splatted."""
        r = sdk.create_subject("acme", props={"intent": "prefer-latest"})
        assert _node_props(sdk, r["id"])["intent"] == "prefer-latest"
        r2 = sdk.create_object("acme thing", props={"intent": "measured"})
        assert _node_props(sdk, r2["id"])["intent"] == "measured"

    def test_an_object_to_subject_redirect_cannot_turn_a_legal_write_into_a_refusal(
            self, sdk, monkeypatch):
        """#12060 review P1. The Subject door validates `status` against ONTOLOGY
        §4.2 and the Object door validates nothing, so a redirect that moves an
        Object write onto the subject layer with an Object-only status raised
        `ValueError: Invalid status 'active' for a :Subject` — a refusal INVENTED
        by a guard, the exact thing `_kind_layer_note` refuses to do three lines
        later. MEASURED before the fix with this same pack guard."""
        guard = Confusion(
            rule="acme:org", when="nameMatches", pattern=r"^acme-org-[0-9]+$",
            redirect="core:organization", fallback="core:Project",
            reason="an org name", declaring_pack="acme")
        monkeypatch.setattr(kg, "_catalog_pack_guards", lambda: (guard,))
        r = sdk.create_object("acme-org-7", status="active")
        assert r.get("id"), "the write was refused by a guard"
        assert r["kind_redirect_skipped"]["reason"] == \
            kg.SKIP_STATUS_NOT_LEGAL_ON_SUBJECT
        assert _labels_kinds(sdk, r["id"])[0] == ["Object"], "the write was moved"
        # …and the tally was CORRECTED, not left claiming an accepted redirect.
        assert {x["outcome"] for x in kind_guard_counts()} == {"skipped"}

    def test_the_same_redirect_is_applied_when_the_status_is_legal(
            self, sdk, monkeypatch):
        """The check is about the STATUS, not about the redirect: a write whose
        status the Subject lifecycle declares still gets redirected."""
        guard = Confusion(
            rule="acme:org", when="nameMatches", pattern=r"^acme-org-[0-9]+$",
            redirect="core:organization", fallback="core:Project",
            reason="an org name", declaring_pack="acme")
        monkeypatch.setattr(kg, "_catalog_pack_guards", lambda: (guard,))
        r = sdk.create_object("acme-org-8", status="live")
        assert _labels_kinds(sdk, r["id"])[0] == ["Subject"]
        assert r["kind_redirect"]["to"]["kind"] == "core:organization"

    def test_a_bare_core_fallback_is_qualified_and_still_degrades(self):
        """#12060 review P2. `redirect` was qualified but `fallback` was not, and
        `kind_layers` cannot route a BARE core name (`entity_route("WorkItem")
        is None`) — so a manifest spelling the fallback bare (the manifest
        convention!) validated clean and then could never degrade. R4's step 1
        was silently dead for that spelling."""
        compiled = kg.pack_guards("acme", [{
            "kind": "concept", "layer": "object", "onConfusion": [{
                "when": "nameMatches", "pattern": r"^dev-issue-[0-9]+$",
                "redirect": "other:thing",     # a depends_on pack, not installed
                "fallback": "WorkItem",        # BARE, as a manifest may spell it
                "reason": "a work item",
            }]}])
        assert compiled[0].fallback == "core:WorkItem"
        assert kg.guard_table_problems(compiled, include_catalog=False) == []
        d = resolve("dev-issue-7", "subject", None,
                    pack_confusions=compiled,
                    installed=frozenset({"core", "acme"}))
        assert d.outcome == GuardOutcome.REDIRECT
        assert d.kind == "core:WorkItem", "the bare fallback was not usable"

    def test_a_caller_cannot_forge_the_durable_redirect_record(self, sdk,
                                                              no_pack_guards):
        """#12060 review P2. `setdefault` let a props bag shadow `declaredKind`
        and `kindRedirectRule`, so the node's attribution disagreed with the
        reply — an audit would read a rule that never fired."""
        r = sdk.create_subject(ISSUE_NAME, declaredKind="core:role",
                               kindRedirectRule="forged-rule",
                               declaredLayer="point")
        props = _node_props(sdk, r["id"])
        assert props["kindRedirectRule"] == "issue-identifier-is-work-item"
        assert props["declaredLayer"] == "subject"
        assert "declaredKind" not in props, "a declaration that never was, recorded"
        assert r["kind_redirect"]["from"] == {"kind": None, "layer": "subject"}

    def test_two_disagreeing_spellings_are_reported_not_rewritten(self, sdk,
                                                                 no_pack_guards):
        """#12060 review P2. `_kind_layer_note` refuses to pick between
        `objectKind="core:organization"` and `object_kind="core:team"` ("nothing
        is dropped, nothing is rewritten") — but the guard runs FIRST, so a
        rewrite here silently discarded one value and pre-empted the report."""
        r = sdk.create_subject(
            "daniel-ospina/agent-infra",
            props={"subjectKind": "core:naturalPerson",
                   "subject_kind": "core:team"})
        assert "kind_redirect" not in r, "the guard rewrote a write it must report"
        assert _labels_kinds(sdk, r["id"])[1] != "core:Project"

    def test_pack_validate_reports_an_incoherent_guard_TABLE(self, tmp_path):
        """#12060 review P2: `guard_table_problems()` had NO runner — only a test
        called it — so a table that had become incoherent was reported by
        nothing, and `pack validate` could print `✓ Clean` over it."""
        bad = """\
  - kind: concept
    layer: object
    onConfusion:
      - when: nameMatches
        pattern: '^thing-[0-9]+$'
        redirect: core:notAKind
        fallback: core:WorkItem
        reason: 'targets a kind nobody can route'
"""
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent), "--json")
        payload = json.loads(r.stdout)
        assert payload["ok"] is False
        assert r.returncode == 1
        assert any("is not routed to one layer" in p
                   for p in payload["guard_problems"]), payload["guard_problems"]

    def test_the_guard_layer_reports_a_disagreement_with_kind_layers(self, tmp_path):
        """The manifest's `layer` is not decorative: when it disagrees with the
        authority, that is a reported defect (at install for its own pack, and in
        the table check for a kind the ambient catalog can route)."""
        bad = GOOD_GUARD.replace("layer: object", "layer: event")
        # the pack's own kind is an objectKind, so `_validate_guards` refuses it
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 1
        assert "is declared on the object layer" in (r.stdout + r.stderr)


# ══ review round 2 ═══════════════════════════════════════════════════════
#
# Every test here FAILED before the fix it names (verified by reverting the fix
# in a scratch copy). Round 1's lessons held: the value of a review finding is
# the test that stops it coming back.

class TestReviewRound2Closed:
    """The second review's findings, each pinned by its own reproduction."""

    def test_a_GUARD_redirect_cannot_turn_a_legal_write_into_an_identity_refusal(
            self, sdk, monkeypatch):
        """R2 P1. The status abstain closed one refusal class; IDENTITY is the
        other. Entity identity is LABEL-SCOPED, so two live carriers of the name
        on the TARGET label — legal on the label the caller chose — made the
        redirect raise `AmbiguousEntityName` where the same write lands unguarded
        and on `origin/main`. MEASURED before the fix: this exact script raised
        from `entity_identity._refuse`, `route-then-refuse` #3633."""
        # Two live :Objects hold the name (a legal double carrier, D2/#8864).
        sdk.create_entity("object", "daniel-ospina/agent-infra",
                          objectKind="core:Project", _server_id="obj-a-1")
        sdk.create_entity("object", "daniel-ospina/agent-infra",
                          objectKind="core:Project", _server_id="obj-b-2")
        # The guard must CROSS labels (a Subject write filed as an Object kind),
        # because that is the only move that reaches the other label's identity.
        guard = Confusion(
            rule="acme:project", when="nameMatches",
            pattern=r"^daniel-ospina/[a-z-]+$", redirect="core:Project",
            fallback="core:WorkItem", reason="a project", declaring_pack="acme")
        monkeypatch.setattr(kg, "_catalog_pack_guards", lambda: (guard,))
        r = sdk.create_subject("daniel-ospina/agent-infra")   # must NOT raise
        assert r.get("id"), "a guard turned a legal write into a refusal"
        assert r["kind_redirect_skipped"]["reason"] == \
            kg.SKIP_TARGET_LABEL_AMBIGUOUS
        # …and the write landed where the caller filed it, unchanged.
        assert _labels_kinds(sdk, r["id"])[0] == ["Subject"]
        assert {x["outcome"] for x in kind_guard_counts()} == {"skipped"}

    def test_a_guard_crossing_labels_discloses_an_existing_split_peer(
            self, sdk, monkeypatch):
        """R2 P2. A guard redirect crosses labels BY DESIGN, so it is the path
        that can always split an entity — and it was the one that withheld the
        disclosure (`_kind_layer_advisory`) while its AX block said `undo: none`.
        MEASURED before the fix: the graph held the name under both labels and
        the reply carried no advisory."""
        sdk.create_subject("acme/corp")          # the peer on the OTHER label
        guard = Confusion(
            rule="acme:project", when="nameMatches", pattern=r"^acme/[a-z]+$",
            redirect="core:Project", fallback="core:WorkItem",
            reason="an org slash-name is a project", declaring_pack="acme")
        monkeypatch.setattr(kg, "_catalog_pack_guards", lambda: (guard,))
        r = sdk.create_subject("acme/corp")
        assert r["kind_redirect"]["to"]["layer"] == "object"
        blob = json.dumps(r.get("advisories") or [])
        assert "ALREADY" in blob and "SECOND" in blob, r.get("advisories")
        # the undo text no longer promises a lossless undo either.
        assert "label-scoped" in r["kind_redirect"]["undo"]

    def test_a_cross_layer_pack_guard_is_NOT_refused_at_install(self, tmp_path):
        """R2 P1. `guard_table_problems` compared the manifest's `layer` (which
        describes the GUARDED kind) against the layer of the REDIRECT — and a
        guard redirects across layers by definition, so every legitimate
        cross-layer pack guard was false-refused. MEASURED before the fix:
        `pack validate` exited 1 on a guard `_validate_guards` accepts."""
        cross = GOOD_GUARD.replace("redirect: core:WorkItem",
                                   "redirect: core:role")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=cross)
        r = _run_cli("pack", "validate", str(manifest.parent), "--json")
        payload = json.loads(r.stdout)
        assert payload["ok"] is True, payload
        assert payload["guard_problems"] == [], payload["guard_problems"]
        assert r.returncode == 0, r.stdout + r.stderr

    def test_the_TABLE_check_reports_a_real_guarded_layer_disagreement(self):
        """R2 P2 (a false green). The old test asserted the INSTALL gate's
        message, so deleting `guard_table_problems`' layer branch left the WHOLE
        FILE green — stated without a case count on purpose: the count this
        sentence used to carry was one of three disagreeing numbers in committed
        artifacts (round-3 review P2). This one calls the table check with a kind
        the ambient catalog CAN route, and fails if that branch is removed."""
        from tortoise.kind_layers import entity_route

        kind = "venture:investment"
        assert entity_route(kind) == "object", entity_route(kind)
        wrong = kg.pack_guards("venture", [{
            "kind": "investment", "layer": "subject",     # the LIE
            "onConfusion": [{"when": "nameMatches", "pattern": "^inv-[0-9]+$",
                             "redirect": "core:WorkItem",
                             "fallback": "core:WorkItem", "reason": "x"}]}])
        problems = guard_table_problems(wrong, include_catalog=False)
        assert any("declared layer 'subject' disagrees" in p for p in problems), \
            problems

    def test_R4_degrade_is_not_withheld_when_the_fallback_shares_the_layer(
            self):
        """R2 P2. `fb_layer != _layer` silently withheld ladder step 1 for every
        guard whose fallback sits on the write's OWN layer — the commonest case,
        since a guard's reach covers both entity doors. MEASURED before the fix:
        one guard, one name, the object door SKIPPED while the subject door
        degraded to `core:WorkItem`."""
        compiled = kg.pack_guards("acme", [{
            "kind": "concept", "layer": "object", "onConfusion": [{
                "when": "nameMatches", "pattern": r"^dev-issue-[0-9]+$",
                "redirect": "other:thing", "fallback": "WorkItem",
                "reason": "a work item", "fromLayers": ["object"]}]}])
        d = resolve(PACK_ONLY_NAME, "object", None, pack_confusions=compiled,
                    installed=frozenset({"core", "acme"}))
        assert d.outcome == GuardOutcome.REDIRECT, d.skipped
        assert d.kind == "core:WorkItem"
        assert d.redirect["degraded"] is True

    def test_a_LITERAL_prefix_survives_a_regex_metacharacter(self):
        """R2 P2. `_matches` compiled every pattern BEFORE branching on the
        predicate, so a `nameStartsWith` prefix containing a metacharacter was
        discarded — the guard silently never fired, and the install gate refused
        it in the same breath, though the predicate promises literal semantics."""
        c = Confusion(rule="acme:draft", when="nameStartsWith",
                      pattern="DRAFT (", redirect="core:WorkItem",
                      fallback="core:WorkItem", reason="a draft")
        assert kg._matches(c, "DRAFT (a)") is True
        d = resolve("DRAFT (a)", "subject", None, pack_confusions=(c,))
        assert d.outcome == GuardOutcome.REDIRECT, d
        # the prefix is still LITERAL: a regex reading would match `DRAFT a`
        assert kg._matches(c, "DRAFT a") is False
        assert guard_table_problems((c,), include_catalog=False) == []

    def test_a_literal_prefix_pack_validates_clean(self, tmp_path):
        """…and the INSTALL gate applies the same rule as the runtime."""
        guard = """\
  - kind: concept
    layer: object
    onConfusion:
      - when: nameStartsWith
        pattern: 'DRAFT ('
        redirect: core:WorkItem
        fallback: core:WorkItem
        reason: 'a draft'
"""
        manifest = _write_pack(tmp_path / "packs", "acme", guards=guard)
        r = _run_cli("pack", "validate", str(manifest.parent))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_abstain_is_idempotent_per_decision(self):
        """R2 P2. The tally correction is a statement about ONE redirect, so a
        second `abstain` on the same decision must not take another `accepted`
        off the tally. MEASURED before the fix: two writes produced
        `accepted 0 / skipped 3` — the second correction ate the first write's
        redirect, i.e. a silent UNDERCOUNT of a redirect that happened."""
        d = resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        first = kg.abstain(d, "probe")
        second = kg.abstain(d, "probe")
        assert first == second, "the second abstain answered with a REDIRECT"
        assert second.outcome == GuardOutcome.SKIPPED
        rows = kind_guard_counts()
        assert sum(r["count"] for r in rows if r["outcome"] == "skipped") == 1
        assert not [r for r in rows if r["outcome"] == "accepted"]

    def test_the_abstain_marker_dies_with_its_own_decision(self):
        """R2 P2 (review at `39615f22`). The marker is keyed on `id(decision)`
        but its first version stored the REPLACEMENT as the weak value, so the
        entry outlived the decision it was about and a later decision allocated
        at the recycled address was served ANOTHER write's abstention.

        The discrimination: `test_abstain_is_idempotent_per_decision` keeps the
        decision alive across both calls, so it cannot see this. Here the
        decision is dropped while the REPLACEMENT the caller holds stays alive —
        the exact shape `_apply_kind_guard` creates, because it hands the
        replacement back to `create_entity` and holds it for the rest of the
        write. MEASURED before the fix: the entry survived (`1` entry, key
        present) and a threaded door reported `accepted: 12 / skipped: 468` over
        480 abstentions. After: `0` entries and `skipped: 480`.
        """
        d = resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        replacement = kg.abstain(d, "probe")
        replacement = kg.abstain(d, "probe")
        address = id(d)
        assert kg._ABSTAINED, "the marker was never recorded — the test is vacuous"
        del d
        gc.collect()
        # The caller still holds `replacement`: that MUST NOT keep the entry
        # alive, because the entry is about the decision that has died.
        assert replacement is not None
        assert address not in kg._ABSTAINED, (
            "the abstain marker outlived the decision it is keyed on — a "
            "decision allocated at this recycled address would be served this "
            "write's skipped rule/target as its own")

    def test_the_next_write_gets_its_own_correction_after_the_first_dies(self):
        """A regression guard: the correction is per WRITE, not per process.

        ⛔ NOT the discriminator for the recycled-id defect — `test_the_abstain_marker_dies_with_its_own_decision`
        is, and it is measured RED on the pre-fix code. This one PASSES on the
        pre-fix code too (measured 10/10), because the second `resolve()` does not
        reliably land on the freed address, so the stale-entry path is not
        exercised here. Its claim is narrower and still worth pinning: a second
        write is neither served the first write's abstention NOR over-corrected —
        the tally must end at exactly two skips and zero accepted, which is what
        a too-eager eviction or a double `_unrecord` would break.
        """
        first = resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        first_replacement = kg.abstain(first, "first write's reason")
        del first
        gc.collect()

        second = resolve(ISSUE_NAME, "subject", None, pack_confusions=())
        second_replacement = kg.abstain(second, "second write's reason")
        assert second_replacement is not first_replacement, (
            "the second write was served the first write's abstention")
        assert second_replacement.skipped["reason"] == "second write's reason", (
            second_replacement.skipped)
        rows = kind_guard_counts()
        assert sum(r["count"] for r in rows if r["outcome"] == "skipped") == 2, rows
        assert sum(r["count"] for r in rows if r["outcome"] == "accepted") == 0, rows

    def test_the_table_check_reports_from_the_DOCUMENTED_shape(self):
        """R2 P2. `guard_table()`'s docstring claimed an unroutable guard was
        OMITTED from the view; the code always listed it with `layer: None`. The
        docstring now says what the code does, and this pins the pairing."""
        # An unroutable target: the guard IS listed (with `layer: None`), and
        # the problem is reported SEPARATELY — the view never pretends the guard
        # is safe, and never claims it is absent.
        unroutable = kg.pack_guards("acme", [{
            "kind": "concept", "layer": "object", "onConfusion": [{
                "when": "nameMatches", "pattern": "^thing-[0-9]+$",
                "redirect": "core:notAKind", "fallback": "core:WorkItem",
                "reason": "nobody can route this"}]}])
        real = kg._catalog_pack_guards
        kg._catalog_pack_guards = lambda: tuple(unroutable)
        try:
            table = guard_table()
            entry = table["core:notAKind"]
            assert entry["layer"] is None, entry
            assert entry["allowedLayers"] == [], entry
            assert entry["commonConfusions"], "the guard was silently omitted"
            assert any("core:notAKind" in p for p in guard_table_problems()), \
                guard_table_problems()
        finally:
            kg._catalog_pack_guards = real

    def test_a_documentKind_guard_is_refused_at_install(self, tmp_path):
        """R2 P2 (dead code). The documentKind refusal sat behind a branch that
        had already claimed every non-empty `kind_ref`, so a pack declaring
        `documentKinds: [contract]` AND a guard on `contract` validated CLEAN —
        the comment said "a guard may not claim it" and nothing enforced it."""
        p = tmp_path / "packs" / "acme"
        p.mkdir(parents=True)
        (p / "manifest.yaml").write_text("""\
namespace: acme
name: Acme Test Pack
version: "0.1.0"
tier: free
ontology:
  extends: core
  documentKinds: [contract]
  guards:
    - kind: contract
      layer: object
      onConfusion:
        - when: nameMatches
          pattern: '^contract-[0-9]+$'
          redirect: core:WorkItem
          fallback: core:WorkItem
          reason: 'a contract is a source genre'
depends_on: []
""")
        r = _run_cli("pack", "validate", str(p))
        assert r.returncode == 1, r.stdout + r.stderr
        assert "documentKind" in (r.stdout + r.stderr)

    def test_changed_is_FALSE_for_a_decision_the_write_must_ignore(self):
        """R2 P2. `changed` had NO caller anywhere in the repo, and it asked
        `kind is not None` — the same question the door asked inline, spelled
        twice. It is now keyed on the OUTCOME (`REDIRECT`), documented as the one
        question the door asks, and used there; this pins the contract. ⚠️ For
        every outcome this module can produce today the two spellings agree (no
        non-REDIRECT decision carries a `kind`), so this test does not
        discriminate them — the outcome term is what keeps it right if one ever
        does."""
        assert resolve(ISSUE_NAME, "subject", None,
                       pack_confusions=()).changed is True
        over = resolve(ISSUE_NAME, "subject", None, intent=INTENT_AS_DECLARED,
                       pack_confusions=())
        assert over.outcome == GuardOutcome.OVERRIDDEN
        assert over.changed is False

    def test_pack_validate_still_prints_a_verdict_when_only_guards_are_wrong(
            self, tmp_path):
        """R2 P2. With `guard_problems` as the ONLY reason, `pack validate`
        printed the guard lines and then NO summary at all — the reader was left
        without a verdict line."""
        bad = GOOD_GUARD.replace("redirect: core:WorkItem",
                                 "redirect: core:notAKind")
        manifest = _write_pack(tmp_path / "packs", "acme", guards=bad)
        r = _run_cli("pack", "validate", str(manifest.parent))
        out = r.stdout + r.stderr
        assert r.returncode == 1
        assert "kind guards" in out
        # the VERDICT line itself — not merely a "✗" the guard section already
        # prints, which is what made the first version of this test pass with
        # the footer deleted (measured).
        assert "kind-guard problem(s)" in out, out


# ══ the safety properties ═════════════════════════════════════════════════

class TestSafetyProperties:
    def test_the_layer_is_derived_from_kind_layers_not_redeclared(self):
        """ONE authority for kind↔layer (#9964/#9675) — the guard table must not
        become a second answer to it.

        ⛔ `layer` is the single layer when there IS one, and `None` when the
        authority declines to answer (2+ layers). It is NOT
        `next(iter(sorted(...)))`, which claimed the alphabetically-first of
        two; and it is NOT `kind_layers.entity_route`, which answers a narrower
        question and would report `None` for every Point/Event kind
        (`statement` → None) — losing a layer this table can answer truthfully.
        Measured at this head: 0 multi-layer kinds, so the difference is latent.
        """
        from tortoise.kind_layers import routing_layers

        multi = []
        for kind, entry in guard_table(include_packs=False).items():
            layers = sorted(routing_layers(kind))
            assert entry["allowedLayers"] == layers, kind
            expected = layers[0] if len(layers) == 1 else None
            assert entry["layer"] == expected, kind
            if len(layers) > 1:
                multi.append(kind)
        # A kind declared on two layers must claim NEITHER … and the population
        # is measured, not assumed, so this assertion cannot go vacuous by the
        # table shrinking.
        for kind in multi:
            assert guard_table(include_packs=False)[kind]["layer"] is None, kind

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


# ══ the third review's findings, each pinned by its own reproduction ═══════

#: A conversation the capture door will not treat as blank (#4897's window and
#: the marked-turn filter both drop short/empty turns).
_CAPTURE_CONV = [
    {"role": "user",
     "content": "the repo daniel-ospina/agent-infra ships a per-kind guard"},
    {"role": "assistant", "content": "noted, that is what the guard is for"},
]


def _capture_extractor(payload: dict):
    """The extractor seam, monkeypatched to a fixed payload — no LLM, no network.

    The entity/point WRITE phase of `_extract_session_v2` is reached only through
    this seam (mirrors `tests/test_4716_capture_operator_ref_remap.py`).
    """
    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        return {"payload": payload, "errors": [], "warnings": [], "stats": {},
                "noops": [], "chain_notes": [], "link_before_create": [],
                "supersessions": [], "minted_kinds": [], "story_arc": "",
                "search": {}, "error_census": {}}

    return _run


def _capture_payload(entity_name: str, entity_kind: str,
                     content: str) -> dict:
    import tortoise.extractor_v2 as ev2

    return {
        "session_id": "s12060_f1", "client_commit_id": "",
        "captured_at": "2026-11-12T00:00:00Z",
        "extractor": {"version": "t", "mode": "byok",
                      "calibration_version": "v2"},
        "summary": "", "story_arc": "", "provenance_refs": [], "sources": [],
        "entities": [{"name": entity_name, "kind": entity_kind}],
        "points": [{"id": ev2._content_id("pt", content), "content": content,
                    "pointKind": "statement", "reason": "NEW",
                    "confidence": 0.5, "c_cal": 0.5,
                    "about_entities": [entity_name], "source_ref": "session.md",
                    "quote": "", "status": "draft", "search_keys": []}],
        "events": [], "operators": [], "supersessions": [],
        "telemetry": {"counts": {"kept": 1}},
    }


class TestReviewRound3Closed:
    """The third review's findings. F1 is the one that mattered: the guard's own
    reply was DISCARDED by the only caller that mints entities in bulk."""

    def test_a_capture_redirect_does_not_drop_the_claim_to_entity_edge(
            self, sdk, monkeypatch):
        """R3 P1. `_extract_session_v2` decided an entity's layer from a
        PRE-WRITE guess, wrote, and kept the guess. `create_entity` can now MOVE
        that write (the guard) or route it (#9964), and the reply says so — so
        the spine suppressed the claim→entity edge as "a Subject the binder
        owns" for a name that had just become an `:Object`, while
        `bind_point_subjects` binds only a name that IS a Subject. The claim lost
        its edge on EITHER channel.

        MEASURED pre-fix (this exact payload, `_landed_entity_facts` neutered to
        the declared facts): the node landed `:Object` / `core:Project` and the
        edges query returned `[]`. Post-fix it returns `aboutObject`. The name
        shape is the live `live-v4` case the module's own docstring quotes
        (`daniel-ospina/agent-infra` filed as a `core:naturalPerson`), i.e. the
        first cohort the feature exists for.
        """
        import tortoise.extractor_v2 as ev2

        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
        name = "daniel-ospina/agent-infra"
        monkeypatch.setattr(ev2, "extract_session_v2", _capture_extractor(
            _capture_payload(name, "core:naturalPerson",
                             "the repo ships a kind guard")))

        r = sdk._capture_session(_CAPTURE_CONV, session_id="s12060_f1")
        assert r.get("ok") is True, r

        g = sdk._get_proj().g
        # 1. the guard applied — the write is no longer the Subject it declared.
        assert [list(row) for row in g.query(
            "MATCH (o:Object {name:$n}) RETURN o.objectKind",
            params={"n": name}).result_set] == [["core:Project"]]
        assert not g.query(
            "MATCH (s:Subject {name:$n}) RETURN s",
            params={"n": name}).result_set
        # 2. THE PIN: the claim still reaches the entity the guard filed it to.
        assert [list(row) for row in g.query(
            "MATCH (p:Point)-[rel:aboutObject|aboutSubject]->(x {name:$n}) "
            "RETURN type(rel)", params={"n": name}).result_set] == [["aboutObject"]]

    def test_a_capture_relation_uses_the_kind_the_node_actually_carries(
            self, sdk, monkeypatch):
        """R3 F1, second symptom. The same stale belief built `entity_index`,
        whose kind the relation leg resolves endpoints with — so a stated
        relation was planned from the DECLARED kind while the node carried the
        redirected one (Layer-1 identity is `(name, kind)`, #11235)."""
        import tortoise.extractor_v2 as ev2

        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
        name = "daniel-ospina/agent-infra"
        payload = _capture_payload(name, "core:naturalPerson",
                                   "the repo ships a kind guard")
        monkeypatch.setattr(ev2, "extract_session_v2",
                            _capture_extractor(payload))
        # `resolve` is what the capture commit builds the index with; drive the
        # helper directly rather than re-deriving it from a graph read, so this
        # pins the INDEX the relation leg consumes.
        from tortoise.sdk import _landed_entity_facts

        redirect = {"node": {"id": "obj-1"},
                    "kind_redirect": {"from": {"kind": "core:naturalPerson",
                                               "layer": "subject"},
                                      "to": {"kind": "core:Project",
                                             "layer": "object"}}}
        assert _landed_entity_facts(redirect, "subject",
                                    "core:naturalPerson") == (
            "object", "core:Project")
        # a re-route reports through the advisory channel instead …
        routed = {"node": {"id": "obj-2"},
                  "advisories": [{"code": "kind_layer_routed",
                                  "kind": "core:Project",
                                  "layer": "subject",
                                  "actual": ["object"]}]}
        assert _landed_entity_facts(routed, "subject", "core:naturalPerson") == (
            "object", "core:Project")
        # … and a write that did NOT move keeps the caller's declaration, which
        # is what makes an abstained/overridden guard a no-op here.
        assert _landed_entity_facts({"node": {"id": "x"}}, "subject",
                                    "core:naturalPerson") == (
            "subject", "core:naturalPerson")

    def test_a_guard_on_an_actionKind_cannot_claim_an_unverifiable_layer(
            self, tmp_path):
        """R3 P3. `_validate_guards` builds its `own_layer` map from the
        objectKinds/pointKinds/eventKinds axes, so for a kind declared ONLY under
        `actionKinds` the kind check passed (`bare in all_pack_kinds`) while the
        LAYER check was skipped — the one guard field nothing verified. MEASURED
        pre-fix: this manifest validated CLEAN. `actionKinds` declares no layer
        at all (ONTOLOGY v3.0 dissolved Action — it names a declared kind, never
        a node label), so the same verdict the `documentKinds` refusal carries
        applies."""
        p = tmp_path / "packs" / "acme"
        p.mkdir(parents=True)
        (p / "manifest.yaml").write_text("""\
namespace: acme
name: Acme Test Pack
version: "0.1.0"
tier: free
ontology:
  extends: core
  actionKinds: [fieldwork]
  guards:
    - kind: fieldwork
      layer: object
      onConfusion:
        - when: nameMatches
          pattern: '^fieldwork-[0-9]+$'
          redirect: core:WorkItem
          fallback: core:WorkItem
          reason: 'a fieldwork handle is not an entity'
depends_on: []
""")
        r = _run_cli("pack", "validate", str(p))
        out = r.stdout + r.stderr
        assert r.returncode == 1, out
        assert "declares no layer" in out, out
        assert "actionKind" in out, out


# ══ 5. review round 4 — the four ways a guard could still lie ═════════════

class TestReviewRound4Closed:
    """Findings from the #12060 landing review, each with the measurement."""

    def test_the_guard_probe_uses_the_DOOR_s_holder_reads(self, sdk):
        """⛔ The refusal-avoidance probe must ask the DOOR's question.

        `ambiguous_holder_ids` shipped reading `live_name_holder_ids` (`name`
        only, no folded arm) while the door refuses on
        `_live_holders_same_entity` (`name` AND `canonical_name`, plus the
        FOLDED spelling). A carrier that only the wider read sees therefore read
        as unambiguous, the redirect was applied, and the door then raised
        `AmbiguousEntityName` — the redirect inventing exactly the refusal it
        probes to avoid. MEASURED before the fix with two legacy carriers in
        which the second holds the name as `canonical_name`.
        """
        from tortoise.entity_identity import (
            ambiguous_holder_ids,
            live_name_holder_ids,
        )

        g = sdk._get_proj().g
        g.query("MATCH (n:Object) DETACH DELETE n")
        g.query("CREATE (:Object {id:'o-12060-a', name:$n, objectKind:'core:WorkItem'})",
                params={"n": "repo/door-probe"})
        g.query("CREATE (:Object {id:'o-12060-b', canonical_name:$n, objectKind:'core:WorkItem'})",
                params={"n": "repo/door-probe"})
        # The narrow read says ONE holder; the door says TWO.
        assert len(live_name_holder_ids(g, "Object", "repo/door-probe")) == 1
        assert ambiguous_holder_ids(g, "Object", "repo/door-probe")

    def test_a_relative_fromKinds_scope_matches_the_canonical_write(self):
        """A manifest names its own kinds BARE; a write declares them canonical.

        Unqualified, `fromKinds: [concept]` compiled to a form no write can
        declare, so the guard was dead in EVERY mode (a non-empty `from_kinds`
        also leaves the NIL mode) — while `pack validate` reported it clean.
        """
        compiled = kg.pack_guards("acme", [{
            "kind": "concept",
            "layer": "object",
            "onConfusion": [{
                "rule": "acme:concept-scope",
                "pattern": r"^concept-[0-9]+$",
                "redirect": "core:WorkItem",
                "fallback": "core:WorkItem",
                "reason": "a concept handle is not an entity",
                "fromKinds": ["concept"],
            }],
        }])
        assert compiled[0].from_kinds == ("acme:concept",)
        # A write that declares the SAME kind, in the spelling a write uses, is
        # matched.
        d = resolve("concept-7", "object", "acme:concept",
                    pack_confusions=compiled, installed=frozenset({"acme"}))
        assert d.outcome == GuardOutcome.REDIRECT
        assert d.kind == "core:WorkItem"

        # ⛔ AND A BARE DECLARATION IS *NOT* MATCHED — the opposite assertion to
        # the one this test first shipped with. Round-4 review P2: an earlier arm
        # added the bare form of a pack kind as a match candidate, so a write
        # declaring bare `concept` matched a guard scoped `acme:concept`. That
        # contradicts `kind_forms`' own rule one function up ("`dev:issue` and
        # `issue` are different strings that may name different things in
        # different namespaces, so stripping an arbitrary namespace would
        # silently widen a guard's reach") and `Confusion.from_kinds`' rule that
        # an explicit declaration the caller made is never overruled by a name
        # guess — and its justification (`named_kind` normalises the ambiguity)
        # was false: `named_kind` strips BLANKS only. The tree treats a bare kind
        # as naming NO pack, so a pack-scoped guard must not claim it.
        # The tree treats a bare kind as naming NO pack, so a pack-scoped guard
        # must not claim it: the write proceeds exactly as declared.
        d = resolve("concept-7", "object", "concept",
                    pack_confusions=compiled, installed=frozenset({"acme"}))
        assert d.outcome == GuardOutcome.ACCEPT, d.outcome

    def test_a_degraded_redirect_is_counted_as_skipped_as_well(self):
        """`accepted` alone cannot answer "did the declared target resolve?".

        Ladder step 1 re-files to the fallback BECAUSE the declared target is
        unusable, and the reply carries both facts. Before this, the tally kept
        only `accepted` (rule, from → fallback) and no `skipped` row at all, so
        a healthy redirect and an uninstalled-target redirect were the same
        number — the one instrument the feature ships could not see its own
        commonest failure.
        """
        guard = Confusion(
            rule="acme:degrades",
            when="nameMatches",
            pattern=r"^ticket-[0-9]+$",
            redirect="other:issue",
            fallback="core:WorkItem",
            reason="a ticket is a work item",
        )
        d = resolve("ticket-9", "subject", None,
                    pack_confusions=(guard,),
                    installed=frozenset({"acme"}))  # `other` is NOT installed
        assert d.outcome == GuardOutcome.REDIRECT
        assert d.redirect["degraded"] is True
        assert d.redirect["to"]["kind"] == "core:WorkItem"
        assert d.skipped["target"] == "other:issue"
        outcomes = {(r["rule"], r["outcome"]): r for r in kind_guard_counts()}
        assert ("acme:degrades", "accepted") in outcomes
        skipped = outcomes.get(("acme:degrades", "skipped"))
        assert skipped, "the unusable declared target was not tallied"
        assert skipped["to"] == "other:issue"

    def test_an_object_to_subject_redirect_with_roleHolder_abstains(
            self, sdk, monkeypatch):
        """The status arm's twin: `roleHolder` is legal on ONE Subject kind.

        The object door stores `roleHolder`; the subject door raises unless the
        kind is `role`. So an object→subject redirect carrying it raised on a
        write that lands unguarded — a refusal the guard invented.
        """
        monkeypatch.setattr(
            kg, "_catalog_pack_guards",
            lambda: (Confusion(
                rule="acme:to-subject",
                when="nameMatches",
                pattern=r"^org-[0-9]+$",
                redirect="core:organization",
                fallback="core:organization",
                reason="an org handle names an organization",
            ),))
        r = sdk.create_object("org-4242", roleHolder="daniel")
        # The write LANDED, as an :Object, unchanged — no ValueError.
        assert r.get("id")
        assert _labels_kinds(sdk, r["id"])[0] == ["Object"]
        assert r["kind_redirect_skipped"]["reason"] == \
            "role-holder-not-legal-on-subject"
        # The negative control: without `roleHolder` the same write redirects.
        r2 = sdk.create_object("org-4243")
        assert _labels_kinds(sdk, r2["id"])[0] == ["Subject"]
        assert r2["kind_redirect"]["to"]["kind"] == "core:organization"

    def test_a_guard_whose_TARGET_is_a_role_kind_abstains_too(self):
        """The `roleHolder` arm is target-INSENSITIVE — pin it, do not assume it.

        Round-4 review P2: `named_kind(decision.kind) != "role"` is True for
        EVERY guard target, because `decision.kind` is always namespaced
        (`core:role`) and `named_kind` strips BLANKS only. So the arm abstains
        whenever `roleHolder` is present, whatever the target — and it must,
        because the Subject door's own rule compares the BARE spelling
        (`named_kind(props.get('subjectKind')) != 'role'`), so a redirect to
        `core:role` carrying `roleHolder` is REFUSED by that door. Allowing the
        redirect here would invent the refusal the arm exists to prevent.

        This test is the coupling's pin: if someone widens the door to accept a
        namespaced `role` and does not widen this arm in the same change, a legal
        redirect becomes a withheld one — and this assertion is where that shows.
        """
        guard = Confusion(
            rule="acme:to-role",
            when="nameMatches",
            pattern=r"^the-owner-[0-9]+$",
            redirect="core:role",
            fallback="core:role",
            reason="a standing description is a role",
        )
        d = kg.resolve("the-owner-7", "object", None,
                       pack_confusions=(guard,), installed=frozenset({"acme"}))
        assert d.outcome == kg.GuardOutcome.REDIRECT
        skip = kg.subject_only_prop_abstention(
            d, {"roleHolder": "daniel"}, "object", frozenset({"live"}))
        assert skip is not None, (
            "a role TARGET must still abstain while the door's role test is "
            "bare-only — see this test's docstring")
        assert skip.skipped["reason"] == kg.SKIP_ROLE_HOLDER_NOT_LEGAL_ON_SUBJECT
        # …and the SAME decision without the prop is accepted untouched, so the
        # abstention is attributable to the prop and not to the target.
        assert kg.subject_only_prop_abstention(
            d, {}, "object", frozenset({"live"})) is None

    def test_the_layer_rule_declines_on_TWO_layers(self, monkeypatch):
        """The `2+ layers => None` arm, driven — 0 core kinds exercise it today.

        Round-4 review P2: the measured population is 0 multi-layer kinds, so
        `expected = layers[0] if len(layers) == 1 else None` and the old
        `next(iter(sorted(layers)), None)` are indistinguishable on the real
        table and the assertion never fires. A rule nothing can reach is a rule
        nothing tests, so the authority is monkeypatched to answer two layers for
        ONE kind: the old expression would report `object` (the alphabetically
        first), the rule must report `None`.
        """
        from tortoise.kind_layers import routing_layers as real_routing

        target = "statement"
        assert len(real_routing(target)) == 1, (
            "the fixture assumes one layer today; pick another kind")

        def _two(kind):
            if kind == target:
                return ("object", "subject")
            return real_routing(kind)

        monkeypatch.setattr("tortoise.kind_layers.routing_layers", _two)
        entry = kg.guard_table(include_packs=False)[target]
        assert entry["allowedLayers"] == ["object", "subject"]
        assert entry["layer"] is None, (
            "a kind the authority places on TWO layers must claim NEITHER — "
            f"got {entry['layer']!r}")

    # ── the install gate: a misspelled KEY, not just a bad value ──────────

    @staticmethod
    def _validate(confusion, guard_extra=None, ont_extra=None,
                  all_pack_kinds=None, depends_on=None):
        from tortoise.pack_registry import PackRegistry

        entry = {"kind": "concept", "layer": "object",
                 "onConfusion": [confusion]}
        entry.update(guard_extra or {})
        ont = {"objectKinds": ["concept"], "guards": [entry]}
        ont.update(ont_extra or {})
        # `_validate_guards` reads no `self` state (asserted by its own shape:
        # every name it touches is a local), so the validator is callable
        # without a loaded catalog — which is the point: this is the INSTALL
        # gate, and it must answer for a manifest that is not installed.
        reg = PackRegistry.__new__(PackRegistry)
        return reg._validate_guards(ont, "acme", depends_on or [],
                                    all_pack_kinds or {"concept"})

    def test_a_misspelled_linkPredicate_KEY_is_refused_at_install(self):
        """`linkpredicate:` (lowercase) is read as ABSENT — the worse outcome.

        Round-4 review P2, measured: the entry validated `✓ Clean` and exited 0,
        `pack_guards` compiled `link_predicate=None`, and the guard then
        redirected to a role kind with NO abstention — filing exactly the orphan
        role `LINK_PREDICATES` exists to prevent. A bad VALUE at least abstains
        for ever (a harmless dead guard); a bad KEY removes the abstention.
        """
        base = {
            "rule": "acme:to-role",
            "pattern": r"^the-owner-[0-9]+$",
            "redirect": "core:role",
            "fallback": "core:role",
            "reason": "a standing description is a role",
        }
        # Correct spelling: accepted.
        assert self._validate({**base, "linkPredicate": "holdsRole"}) == []
        # Lowercased key: refused, and the message names it.
        errs = self._validate({**base, "linkpredicate": "holdsRole"})
        assert errs and "linkpredicate" in " ".join(errs), errs

    def test_a_misspelled_fromKinds_KEY_is_refused_at_install(self):
        """`fromKind:` (no `s`) silently turned a SCOPED guard into a NIL one.

        Round-4 review P2: the scoped guard fires only when the write DECLARED
        one of the listed kinds; with the key misspelled the list is empty, which
        is the NIL mode — so the guard fires on writes that named NOTHING, the
        exact opposite of the author's intent, at install-clean.
        """
        base = {
            "rule": "acme:scoped",
            "pattern": r"^concept-[0-9]+$",
            "redirect": "core:WorkItem",
            "fallback": "core:WorkItem",
            "reason": "a concept handle is not an entity",
        }
        assert self._validate({**base, "fromKinds": ["concept"]}) == []
        errs = self._validate({**base, "fromKind": ["concept"]})
        assert errs and "fromKind" in " ".join(errs), errs

    def test_the_degraded_SKIPPED_row_is_withdrawn_by_an_abstention(self):
        """A degraded redirect's `skipped` row names a target the `_unrecord` missed.

        Round-5 review P2, measured: `_redirect` records TWO rows for a degraded
        redirect — `accepted` for the fallback, `skipped` for the declared target
        that was unusable — and `abstain` withdrew only the `accepted` row (it
        keys on `decision.kind`, which for a degraded decision IS the fallback).
        A degraded-then-abstained write therefore left a `skipped` row for
        `other:issue` in the tally while NO redirect happened at all: a counter
        reporting a redirect that did not happen, which is the one thing
        `abstain`'s own docstring forbids — and it poisons the exact question the
        row was added to answer, because a target that failed to resolve for a
        write the door then refused to redirect is not an installed-target gap.
        """
        guard = Confusion(
            rule="acme:deg-abstain",
            when="nameMatches",
            pattern=r"^ticket-[0-9]+$",
            redirect="other:issue",       # not installed -> degrades
            fallback="core:WorkItem",
            reason="a ticket is a work item",
        )
        d = kg.resolve("ticket-9", "subject", None,
                       pack_confusions=(guard,),
                       installed=frozenset({"acme"}))
        assert d.outcome == kg.GuardOutcome.REDIRECT
        assert d.redirect["degraded"] is True
        assert d.skipped["target"] == "other:issue"
        kg.abstain(d, kg.SKIP_STATUS_NOT_LEGAL_ON_SUBJECT, layer="subject")
        rows = [r for r in kg.kind_guard_counts()
                if r["rule"] == "acme:deg-abstain"]
        assert [r for r in rows if r["to"] == "other:issue"] == [], (
            "the declared target's `skipped` row survived an abstention — the "
            f"redirect it describes never happened: {rows}")
        # …and the abstention's OWN row is still there, so the fix withdrew the
        # phantom and not the measurement.
        assert [r for r in rows if r["outcome"] == "skipped"
                and r["to"] == "core:WorkItem"], rows

    def test_a_fromKinds_scope_on_a_DOCUMENT_kind_is_ACCEPTED(self):
        """⛔ A gate must not refuse a scope the runtime DEMONSTRABLY fires.

        This test replaced the opposite assertion, which round 5 shipped on the
        theory that `_named_layer_kinds` excluded `documentKinds` and therefore no
        write could declare one. That function reads the layer PROP's VALUE
        (`objectKind=…`), and the value is caller input over an open vocabulary —
        so a document kind CAN be declared and the guard does fire. Measured
        (round-6 review P2): with `documentKinds: [brief]` + `fromKinds: [brief]`,
        `create_object("brief-9", objectKind="acme:brief")` REDIRECTED while the
        narrowed gate had refused it at install. A false block is the same
        category of lie as the dead guard it was meant to catch.

        The gate's job is to refuse a scope that can NEVER fire — "no pack declares
        this kind on ANY axis", which is `all_pack_kinds`. Keeping this test makes
        the false block un-reintroducible.
        """
        errs = self._validate(
            {"rule": "acme:brief", "pattern": r"^brief-[0-9]+$",
             "redirect": "core:WorkItem", "fallback": "core:WorkItem",
             "reason": "x", "fromKinds": ["brief"]},
            ont_extra={"documentKinds": ["brief"]},
            all_pack_kinds={"concept", "brief"},
        )
        assert errs == [], (
            "a scope on a kind this pack DECLARES (on a non-entity axis) is live "
            f"at runtime — refusing it is a false block: {errs}")

        # …and the gate still refuses one no axis declares (a typo).
        errs = self._validate(
            {"rule": "acme:conpekt", "pattern": r"^conpekt-[0-9]+$",
             "redirect": "core:WorkItem", "fallback": "core:WorkItem",
             "reason": "x", "fromKinds": ["conpekt"]},
            all_pack_kinds={"concept", "brief"},
        )
        assert errs and "fromKinds" in " ".join(errs), errs

    def test_the_whole_CORE_vocabulary_is_accepted_as_a_scope(self):
        """⛔ NEITHER core source alone is the vocabulary — the UNION is.

        Round-7 review P1: the arm was built from `CORE_KINDS` alone, which
        contains NO §5 Subject kind (`organization`, `team`, `role`,
        `naturalPerson`, `legalPerson`) — those live only in `core_kind_names()`
        (measured: 33 forms outside `CORE_KINDS`, and 44 outside
        `core_kind_names()`). So `fromKinds: [core:organization]` was REFUSED as
        dead while the scope demonstrably fires — and the Subject vocabulary is
        precisely the misfiling this feature exists to fix (a document filed as
        `core:role`). The same file already accepted `core:organization` as a
        `fallback`, so it refused as a scope what it accepted one field over.
        """
        for kind in ("core:organization", "core:role", "core:team",
                     "core:naturalPerson", "core:legalPerson", "core:Project",
                     "core:WorkItem", "core:brief"):
            errs = self._validate(
                {"rule": "acme:vocab", "pattern": r"^v-[0-9]+$",
                 "redirect": "core:WorkItem", "fallback": "core:WorkItem",
                 "reason": "x", "fromKinds": [kind]},
                all_pack_kinds={"concept"},
            )
            assert errs == [], f"{kind} is a declared core kind: {errs}"

    def test_the_NIL_bucket_is_not_a_scope(self):
        """`other` is IN the vocabulary but no write can DECLARE it.

        Round-7 review P2: `other`/`core:other` sat inside the accepted
        vocabulary, so a scope on it installed clean — and could never fire,
        because `_named_layer_kinds` drops the NIL and the door hands the guard no
        declared kind. The empty list IS the NIL mode, so the refusal names it.
        """
        for nil in ("other", "core:other"):
            errs = self._validate(
                {"rule": "acme:nil", "pattern": r"^n-[0-9]+$",
                 "redirect": "core:WorkItem", "fallback": "core:WorkItem",
                 "reason": "x", "fromKinds": [nil]},
                all_pack_kinds={"concept"},
            )
            assert errs and "NIL bucket" in " ".join(errs), (nil, errs)

    def test_an_OWN_NAMESPACE_redirect_typo_is_refused_at_install(self, tmp_path):
        """The table ABSTAINS for a pack target, so nothing else asks.

        Round-7 review P2, measured on a real manifest: `redirect: acme:konpekt`
        printed `\u2713 Clean` and exited 0, because the redirect arm checked only
        the NAMESPACE, and `guard_table_problems` deliberately skips an unrouted
        PACK target (`_layer_of_kind` reads the ambient catalog, so a pack kind it
        cannot see would false-refuse every legitimate cross-pack guard). The
        guard then degraded to `fallback` on every match, silently.

        This pack's own namespace is the ONE kind list install has in hand, so it
        is knowable here \u2014 and it is the ONLY namespace checked here: a `core`
        typo is the table's question (it answers in the same verdict), and a
        `depends_on` namespace is not this pack's to enumerate.
        """
        base = {"rule": "acme:red", "pattern": r"^r-[0-9]+$",
                "fallback": "core:WorkItem", "reason": "x"}
        # the real spelling is silent, so the refusal discriminates
        assert self._validate({**base, "redirect": "acme:brief"},
                              all_pack_kinds={"concept", "brief"}) == []
        errs = self._validate({**base, "redirect": "acme:konpekt"},
                              all_pack_kinds={"concept", "brief"})
        assert errs and "redirect" in " ".join(errs), errs
        # a depends_on target names a namespace this pack does not own: its kind
        # list is not knowable here, so it must NOT be refused (else the arm is a
        # false block on the cross-pack case it exists to allow)
        assert self._validate({**base, "redirect": "venture:investment"},
                              depends_on=["venture"],
                              all_pack_kinds={"concept"}) == []

        # ⛔ END-TO-END, on a real manifest — because the round's FIRST version of
        # this test hand-built a `Confusion` (declaring_pack=None) and therefore
        # "passed" by exercising the CORE branch, never the path a manifest takes
        # (round-7 review P2, measured: the real one printed `✓ Clean`, exit 0).
        def _cli(target):
            d = tmp_path / target.replace(":", "_")
            guards = f"""\
  - kind: concept
    layer: object
    onConfusion:
      - when: nameMatches
        pattern: '^r-[0-9]+$'
        redirect: {target}
        fallback: core:WorkItem
        reason: x
"""
            man = _write_pack(d / "packs", "acme",
                              object_kinds="concept, widget", guards=guards)
            return _run_cli("pack", "validate", str(man.parent))

        ok = _cli("acme:widget")          # a kind this pack really declares
        assert ok.returncode == 0, ok.stdout + ok.stderr
        bad = _cli("acme:konpekt")
        assert bad.returncode == 1, bad.stdout + bad.stderr
        assert "redirect: 'acme:konpekt'" in bad.stdout, bad.stdout

    def test_a_fromKinds_scope_naming_no_CORE_kind_is_refused(self):
        """`core:WrokItem` is a typo the own-namespace arm cannot see.

        Round-5 review P2, measured: the arm only covered this pack's OWN
        namespace, so a misspelled CORE scope validated clean and compiled to a
        scope that matches nothing — the same dead-guard class, through the one
        namespace with no pack to check it against.
        """
        errs = self._validate(
            {"rule": "acme:core-typo", "pattern": r"^x-[0-9]+$",
             "redirect": "core:WorkItem", "fallback": "core:WorkItem",
             "reason": "x", "fromKinds": ["core:WrokItem"]},
        )
        assert errs and "core" in " ".join(errs), errs
        # the real one still passes
        assert self._validate(
            {"rule": "acme:core-ok", "pattern": r"^x-[0-9]+$",
             "redirect": "core:WorkItem", "fallback": "core:WorkItem",
             "reason": "x", "fromKinds": ["core:WorkItem"]},
        ) == []

    def test_a_misspelled_key_on_the_GUARD_itself_is_refused(self):
        """The same rule one level up: `onConfusions:` (plural) loses every entry."""
        errs = self._validate(
            {"rule": "acme:r", "pattern": "^x-[0-9]+$",
             "redirect": "core:WorkItem", "fallback": "core:WorkItem",
             "reason": "x"},
            guard_extra={"layer": "object", "onConfusions": []})
        assert errs and "onConfusions" in " ".join(errs), errs
