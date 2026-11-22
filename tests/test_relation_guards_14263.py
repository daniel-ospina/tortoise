"""#14263 — does the write path use the packs' BUSINESS LOGIC for an edge?

`#14263` asks a correctness question about the product's ontology promises: do
the MCP and SDK doors consult the installed packs' declared `relations` /
`kindDefs` before they let a caller **write** and **connect** things? The
extractor does, exhaustively (`extractor_v2` derives each kind's edgemates from
the declared `relations` and closes them to a fixpoint); the write path had no
**kind-pair** leg at all.

The SILENT case the leg closes is the `label` on the OPERATOR and INGEST doors: a
pack declares `assignedTo` / `contains` / `addresses`, so the `#1934`/`#7551`
predicate leg says nothing (the WORD is declared) and nothing asked whether these
two KINDS are a pair the pack declares that word for. ⛔ It is NOT
`link_entities("blocks", …)`: that routes to `_EdgeHandlers.create_edge`, whose
`_VALID_EDGE_PREDICATES` allow-list is CLOSED and raises `Unknown predicate:`
before writing — the residual the `69a9fae6c8` review (F4) corrected here in
both places the wrong example was told.

What this file pins, and why each test is here rather than in a docstring:

1. **The verdict table** — accept / normalised / undeclared / reversed /
   abstain, over the SHIPPED packs, not over a fixture vocabulary. A guard
   tested only against its own fixture proves the dict works, not that it
   answers the catalog.
2. **The AX shape**, `docs/ax/ax-principles.md` §1:
   * Rule 1 class 1 (a re-spelling of the same value) is **silent**;
   * Rule 1 class 2 (a claim about what the graph now contains) is **echoed**;
   * §2's no-flattening rule — "declared for the reverse pair" is a DIFFERENT
     reported state from "no declared relation at all";
   * Rule 2's **admission test** — a refusal whose third part cannot be written
     must not exist. So: no input reaches a refusal, and no input reaches a
     semantic redirect either (substituting the predicate rewrites the edge's
     MEANING — the class `#9963` measured).
3. **The doors** — the leg fires from `link_entities` (the SDK's one linking
   verb, whose plain-edge arm is `_create_edge`), `_create_direct_edge` and
   `_create_operator`, and the write LANDS in every reported case.
4. **Abstention** — an unreadable declaration set, an unknown predicate and an
   unresolved endpoint kind all say nothing and refuse nothing (`#12060` R4: a
   write must not depend on which packs happen to be installed).
5. **No surface change** — the new SDK helpers are private, so the MCP tool
   registry and the public SDK method set are untouched (repo `AGENTS.md`:
   an MCP/SDK surface change needs Daniel's approval; changing what an existing
   door VALIDATES does not).
6. **The published contract** — `docs/INGEST_CONTRACT.md` §3.3's example IS an
   emitted payload, so the block a client branches on cannot drift from the code
   that fills it (review F2).

Runnable embedded (no Docker)::

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_relation_guards_14263.py -q
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import relation_guards as rg
from tortoise.relation_guards import (
    ABSTAIN,
    ACCEPT,
    LABEL_ENDPOINTS,
    NORMALISED,
    REVERSED_PAIR,
    UNDECLARED_PAIR,
    canonical_predicate,
    declared_relation_pairs,
    relation_pair_verdict,
)
from tortoise.sdk import TortoiseSDK

TEST_GRAPH = "tortoise_test_14263_relation_pairs"

#: The outcomes the module can PRODUCE — the CONTRACT, stated here so the module
#: is graded against it rather than against itself. `_produced_outcomes()` reads
#: the module and must EQUAL this, so a new outcome constant added to
#: `relation_guards.py` (a `refuse()`, a `redirect()`) reds this by name instead
#: of being pinned to a literal set that can never vary.
PRODUCED_OUTCOMES = {
    ACCEPT, NORMALISED, UNDECLARED_PAIR, REVERSED_PAIR, ABSTAIN,
}


def _produced_outcomes() -> set[str]:
    """The outcome VALUES the module actually defines, read from the module.

    Derived by the module's own naming convention — every module-level UPPERCASE
    string that is not an abstention REASON id (``ABSTAIN_*``, a *why* not a
    *what*) and not a RULE id (``RULE_*``, which names a payload's rule field) is
    an outcome. Reading it rather than restating it is the point: the assertion
    that uses this must be able to RED, which a test-local literal compared with
    another test-local literal (``X >= X``) can never do — it stayed green under
    every possible edit to `relation_guards.py`, including one that added a
    ``refuse()`` outcome, while reading as the pin for the no-refusal property
    (`69a9fae6c8` review, F6).
    """
    return {
        value for name, value in vars(rg).items()
        if name.isupper() and isinstance(value, str)
        and not name.startswith(("ABSTAIN_", "RULE_"))
    }


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def sdk(tmp_path):
    """A private embedded graph — the pair leg needs a real graph only to read
    the two endpoints' kinds, and an embedded store serves that."""
    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(str(tmp_path / "pairs.db"), namespace=TEST_GRAPH,
                    event_log_path=str(events / "events.jsonl"))
    yield s
    with contextlib.suppress(Exception), s._live_write_scope("test-cleanup-14263"):
        s._get_proj().g.query("MATCH (n) DETACH DELETE n")
    s.close()


def _obj(sdk, name, kind):
    """A ``:Object`` of a pack kind — the shape a declared relation names."""
    out = sdk._create_object(name, objectKind=kind)
    assert out.get("id"), out
    return out["id"]


def _decl(new="DEV", old="API"):
    """A tiny, fully-controlled declaration set.

    Used only where the test is about the VERDICT ALGEBRA rather than about the
    catalog — the shipped-pack tests below are the ones that prove the guard
    answers the real manifests.
    """
    return {new: ((old, "DB"),)}


# ══ 1. the verdict table ══════════════════════════════════════════════════


class TestVerdictTable:
    """The five outcomes, decided over the SHIPPED packs.

    `packs/dev` declares ``dependsOn | dev:api -> dev:database``; that one row
    is enough to exercise accept, reversed and undeclared without a fixture.
    """

    def test_declared_pair_is_accepted_silently(self):
        v = relation_pair_verdict("dependsOn", "dev:api", "dev:database")
        assert v.outcome == ACCEPT
        assert v.payload is None, "an accept must say nothing — there is no state to report"

    def test_reverse_orientation_is_a_DIFFERENT_state(self):
        """AX §2: do not flatten "no such relation" into "wrong way round"."""
        v = relation_pair_verdict("dependsOn", "dev:database", "dev:api")
        assert v.outcome == REVERSED_PAIR
        assert v.payload["rule"] == rg.RULE_REVERSED
        # the correction IS nameable here, so the block names it
        assert "from/to swapped" in v.payload["message"]

    def test_undeclared_pair_is_reported_not_refused(self):
        # `dev:code` -> `dev:bug` has NO declared relation in either orientation
        # (the shipped rows are `fixes | dev:pullRequest -> dev:bug` and
        # `causes | dev:bug -> dev:incident`), so the block reaches the
        # no-alternative branch — the one where nothing can be corrected.
        v = relation_pair_verdict("dependsOn", "dev:code", "dev:bug")
        assert v.outcome == UNDECLARED_PAIR
        assert v.payload["rule"] == rg.RULE_UNDECLARED
        assert v.payload["outcome"] != REVERSED_PAIR, (
            "an undeclared pair must not be reported as a reversed one")
        # §2: the reported state must name what IS declared
        assert {"fromKind": "dev:api", "toKind": "dev:database"} in \
            v.payload["declaredFor"]
        assert v.payload["declaredBetweenTheseKinds"] == []
        assert "nothing to correct" in v.payload["message"]

    def test_the_declared_alternative_for_the_pair_is_named(self):
        """`dev` declares `decomposesInto | dev:epic -> dev:issue`.

        The caller wrote `dependsOn`; the right home for the PAIR is a different
        PREDICATE, i.e. a different meaning — so it is named, never substituted.
        """
        v = relation_pair_verdict("dependsOn", "dev:epic", "dev:issue")
        assert "decomposesInto" in v.payload["declaredBetweenTheseKinds"]
        assert v.payload["predicate"] == "dependsOn", (
            "the predicate must not be rewritten — that would be a semantic guess")

    def test_predicate_re_spelling_is_normalised(self):
        v = relation_pair_verdict("grounded_in", "agent-ops:rule",
                                 "agent-ops:rationale")
        assert v.outcome == NORMALISED
        assert v.predicate == "groundedIn", "the DECLARED spelling is what gets written"
        assert v.payload is None, "AX Rule 1 class 1: a re-spelling is SILENT"

    def test_bare_endpoint_kind_is_the_same_kind_in_another_form(self):
        v = relation_pair_verdict("contains", "product", "feature")
        assert v.outcome == ACCEPT

    def test_two_namespaces_never_fold_into_each_other(self):
        """`dev:issue` and `pm:issue` are different kinds sharing a local name."""
        assert not rg._kind_equivalent("dev:issue", "pm:issue")
        # A LABEL-spelled declaration admits a node of that LABEL and nothing
        # else — it is a wildcard over one label, not over "anything bare".
        assert rg._kind_equivalent("dev:issue", "Object", "Object")
        assert not rg._kind_equivalent("dev:issue", "Object", "Point")
        assert not rg._kind_equivalent("dev:issue", "Object", None)

    def test_a_label_spelled_endpoint_matches_the_node_label(self):
        """`packs/dev` declares `ownedBy | core:goal -> Subject`.

        `Subject` there is the node LABEL, not a kind. Comparing kinds only
        would report every such write as undeclared — a false report on the door
        whose value is that its reports are trustworthy.
        """
        v = relation_pair_verdict("ownedBy", "core:goal", "core:naturalPerson",
                                 from_label="Object", to_label="Subject")
        assert v.outcome == ACCEPT
        # …and without the label the same write is honestly reported
        v2 = relation_pair_verdict("ownedBy", "core:goal", "core:naturalPerson")
        assert v2.outcome == UNDECLARED_PAIR

    def test_a_declarationOnly_row_offers_no_pair(self):
        """`packs/project-management`'s `onBoard` declares `from: Object / to:
        Object` — a LABEL pair matching EVERY object-to-object edge — and its
        own reason says it "binds no write path"."""
        declared = declared_relation_pairs()
        assert declared is not None
        assert declared.get("onBoard") == ()
        assert canonical_predicate("onBoard", declared) == "onBoard", (
            "the word stays in the vocabulary — only its pair is withheld")
        v = relation_pair_verdict("onBoard", "core:a", "core:b",
                                 from_label="Object", to_label="Object")
        assert v.outcome == ABSTAIN
        v2 = relation_pair_verdict("dependsOn", "dev:database", "dev:api")
        assert "onBoard" not in v2.payload["declaredBetweenTheseKinds"], (
            "a declarationOnly row must not be offered as a declared alternative")


# ══ 2. abstention — the fail-open direction ═══════════════════════════════


class TestAbstention:
    def test_unknown_predicate_abstains_without_a_graph_read(self):
        # The #1934/#7551 predicate leg owns this case; warning twice for one
        # fact is how a warning stops carrying information.
        for predicate in ("related", "references", "inventedVerb",):
            v = relation_pair_verdict(predicate, "dev:api", "dev:database")
            assert v.outcome == ABSTAIN, predicate
            assert v.payload is None

    def test_unresolved_endpoint_kind_abstains(self):
        v = relation_pair_verdict("dependsOn", None, "dev:database")
        assert v.outcome == ABSTAIN
        assert v.reason == rg.ABSTAIN_NO_KINDS
        v = relation_pair_verdict("dependsOn", "dev:api", "   ")
        assert v.outcome == ABSTAIN

    def test_a_pair_outside_the_declared_vocabulary_abstains(self):
        """The scope gate: no pack declares ANY pair involving these kinds.

        `addresses` is a declared predicate with many pairs, and `evidence`/
        `statement` are BASE point kinds no pack declares. The declarations
        cannot speak to the pair, so the honest answer is silence — a report
        here would put a warning on the ordinary
        `create_operator(label='addresses')` write between two Base points,
        which is the noise class that makes a warning stop carrying meaning.
        """
        for predicate in ("addresses", "groundedIn", "contains", "dependsOn"):
            v = relation_pair_verdict(predicate, "evidence", "statement")
            assert v.outcome == ABSTAIN, predicate
            assert v.reason == rg.ABSTAIN_OUTSIDE_SCOPE

    def test_one_endpoint_in_scope_is_enough_to_judge(self):
        """A pack kind on ONE side puts the packs' domain language in play."""
        v = relation_pair_verdict("contains", "product-strategy:product",
                                 "statement")
        assert v.outcome == UNDECLARED_PAIR

    def test_unreadable_declarations_abstain(self, monkeypatch):
        monkeypatch.setattr(rg, "declared_relation_pairs", lambda: None)
        v = relation_pair_verdict("dependsOn", "dev:api", "dev:database")
        assert v.outcome == ABSTAIN
        assert v.reason == rg.ABSTAIN_UNREADABLE

    def test_no_predicate_abstains(self):
        for predicate in (None, "", "   ", 42):
            assert relation_pair_verdict(predicate, "a", "b").outcome == ABSTAIN


# ══ 3. the AX admission test — no refusal, no redirect ════════════════════


class TestAXAdmission:
    """`docs/ax/ax-principles.md` §1 Rule 2 + the admission test.

    *If the third part cannot be written, the surface must not refuse.* For an
    undeclared pair nothing on the write side can be corrected — the pack
    declares no relation for it — so the write is accepted, recorded and
    reported. A refusal here would be the system failing the caller.
    """

    def test_no_refusal_outcome_exists(self):
        assert "refuse" not in dir(rg)
        assert "Refused" not in dir(rg)
        # ⛔ This is the non-vacuous half: `_produced_outcomes()` READS the module,
        # so a new outcome constant (`REFUSE = "refuse"`, a `REDIRECT`) makes the
        # two sets differ and reds here BY VALUE. The literal set stays on the
        # right-hand side as the CONTRACT — deleting a shipped outcome reds too.
        assert _produced_outcomes() == PRODUCED_OUTCOMES, (
            "the module's outcome vocabulary drifted from the declared contract: "
            f"module={sorted(_produced_outcomes())} "
            f"contract={sorted(PRODUCED_OUTCOMES)}")

    def test_the_module_declares_why_redirect_is_not_produced(self):
        doc = rg.__doc__ or ""
        assert "redirect" in doc.lower()
        assert "MEANING" in doc or "meaning" in doc

    def test_no_input_reaches_a_refusal(self, monkeypatch):
        """A hostile grid: every verdict is a value, nothing raises."""
        kinds = ["dev:api", "dev:database", "dev:epic", None, "", "  ",
                 "made:up", "issue", "Object", 7]
        predicates = ["dependsOn", "DEPENDSON", "groundedIn", "contains",
                      "related", "", None, "x" * 300]
        for predicate in predicates:
            for fk in kinds:
                for tk in kinds:
                    v = relation_pair_verdict(predicate, fk, tk)
                    assert v.outcome in PRODUCED_OUTCOMES, (predicate, fk, tk)
                    assert v.accepted or v.reportable or v.outcome == ABSTAIN

    def test_every_reported_block_carries_the_ax_shape(self):
        """One stable shape everywhere: a client branches on data, not prose."""
        for outcome_args in (
            ("dependsOn", "dev:epic", "dev:issue"),
            ("dependsOn", "dev:database", "dev:api"),
        ):
            payload = relation_pair_verdict(*outcome_args).payload
            assert payload is not None
            for key in ("outcome", "predicate", "from", "to", "rule",
                        "declaredFor", "declaredBetweenTheseKinds",
                        "override", "undo", "message"):
                assert key in payload, key
            assert payload["override"] is None
            assert payload["undo"].startswith("none")
            assert payload["message"].startswith("relation '")

    def test_warning_shape_rerenders_one_judgement(self):
        payload = relation_pair_verdict("dependsOn", "dev:epic", "dev:issue").payload
        w = rg.warning_shape(payload)
        assert w["code"] == "undeclared_relation_pair"
        assert w["relation_declaration"]["rule"] == payload["rule"]
        assert w["outcome"] == payload["outcome"]

    def test_a_REVERSED_report_does_not_arrive_under_the_UNDECLARED_code(self):
        """Round-2 review F2 — `code` must not flatten the two reported states.

        `warning_shape` hardcoded one `code`, so a `reversed_pair` (the pack
        DECLARES this predicate for the reverse pair) arrived as
        `undeclared_relation_pair` for the consumer §2's rule protects: the one
        that reads only `warnings[].code` and never unrolls
        `relation_declaration`. `outcome` distinguished them, so nothing FALSE
        was said — but the code said "no declared relation at all" about a
        declared one.
        """
        payload = relation_pair_verdict(
            "dependsOn", "dev:database", "dev:api").payload
        assert payload["outcome"] == REVERSED_PAIR
        assert rg.warning_shape(payload)["code"] == "reversed_relation_pair"


# ══ 4. the shipped packs, read as the guard reads them ═══════════════════


class TestShippedDeclarations:
    def test_the_declaration_set_is_the_catalog_not_a_fixture(self):
        declared = declared_relation_pairs()
        assert declared is not None
        # one row from four different packs, so a regression that stopped
        # reading one namespace's relations is visible here
        assert ("dev:api", "dev:database") in declared["dependsOn"]
        assert ("product-strategy:product", "product-strategy:feature") in declared["contains"]
        assert ("agent-ops:rule", "agent-ops:rationale") in declared["groundedIn"]
        assert ("venture:investment", "portfolio:mandate") in declared["constrainedBy"]

    def test_the_label_set_is_the_ontology_labels(self):
        """The mirror is pinned against its authority (`pack_state`), so a label
        added there is a red test here rather than a silent non-match.

        ⛔ NO bilateral subtraction (#14284 review F3). The previous draft read
        `set(_ONTOLOGY_LABELS) - {"Document"} == set(LABEL_ENDPOINTS) - {"Document"}`,
        which subtracted the SAME element from both sides — so `Document`, the one
        slot the module's own comment calls out, was unpinned in BOTH directions:
        deleting it from `LABEL_ENDPOINTS` left this green. The two sets are the
        same six today, so plain equality pins every slot, that one included; the
        explicit membership line states the intent rather than relying on it.
        """
        from tortoise.pack_state import _ONTOLOGY_LABELS

        assert set(_ONTOLOGY_LABELS) == set(LABEL_ENDPOINTS)
        assert "Document" in LABEL_ENDPOINTS

    def test_the_relationship_answer_does_not_depend_on_which_packs_are_loaded(
            self, monkeypatch):
        """A registry that cannot be read abstains — never refuses, never guesses."""
        import tortoise.domain_loader as dl

        monkeypatch.setattr(dl, "_get_registry", lambda: None)
        assert declared_relation_pairs() is None
        v = relation_pair_verdict("dependsOn", "dev:api", "dev:database")
        assert v.outcome == ABSTAIN


# ══ 5. the doors ══════════════════════════════════════════════════════════


class TestDoors:
    """Every connect door, driven through the SDK on a live embedded graph.

    `link_entities` is the SDK's one linking verb; its plain-edge arm is
    `_create_edge`, which is also what `ingest`'s connection writer reaches.
    """

    def test_the_plain_edge_door_reports_an_undeclared_pair_and_WRITES(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        epic = _obj(sdk, "Epic", "dev:epic")
        issue = _obj(sdk, "Issue", "dev:issue")

        # accept first — same predicate, declared pair
        ok = sdk.link_entities("dependsOn", api, db)
        assert ok["created"] is True
        assert "relation_declaration" not in ok, (
            "an accept must be silent — no field, no warning")

        out = sdk.link_entities("dependsOn", epic, issue)
        assert out["created"] is True, "the write must LAND (#1934 warn-not-block)"
        declaration = out["relation_declaration"]
        assert declaration["outcome"] == UNDECLARED_PAIR
        assert declaration["from"] == "dev:epic"
        assert declaration["to"] == "dev:issue"
        # the #1934 shape a consumer already reads, carrying the same judgement
        assert [w["code"] for w in out["warnings"]] == ["undeclared_relation_pair"]
        assert out["warnings"][0]["relation_declaration"]["rule"] == rg.RULE_UNDECLARED
        # and the edge really is in the graph
        assert _edge_exists(sdk, epic, issue, "dependsOn")

    def test_the_plain_edge_door_reports_reversal_distinctly(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk.link_entities("dependsOn", db, api)
        assert out["created"] is True
        assert out["relation_declaration"]["outcome"] == REVERSED_PAIR
        assert out["relation_declaration"]["rule"] == rg.RULE_REVERSED
        # …and the door's warning CODE says so too, end to end (round-2 review
        # F2): a consumer that reads only `warnings[].code` must not be told
        # "no declared relation at all" about a pair the packs DO declare.
        assert [w["code"] for w in out["warnings"]] == ["reversed_relation_pair"]

    def test_a_predicate_re_spelling_is_written_as_declared_and_says_nothing(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk.link_entities("DEPENDSON", api, db)
        assert out["created"] is True
        assert out["edge"]["relation"] == "dependsOn", (
            "AX Rule 1 class 1: the DECLARED spelling is what gets stored")
        assert "relation_declaration" not in out
        assert "warnings" not in out, (
            "a re-spelling of the same value is SILENT — a receipt would ask "
            "the caller to re-express what we already understood")

    def test_an_endpoint_with_no_kind_abstains(self, sdk):
        """Unknown kinds are not evidence of an undeclared pair."""
        from tortoise.projection import _live_write_scope

        with _live_write_scope("test-fixture-14263"):
            sdk._get_proj().g.query(
                "CREATE (:Object {id:'unk_a'}), (:Object {id:'unk_b'})")
        out = sdk.link_entities("dependsOn", "unk_a", "unk_b")
        assert out["created"] is True
        assert "relation_declaration" not in out

    def test_the_operator_door_reports(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_operator("IMPL", api, [db], label="contains")
        assert out.get("id"), "the operator must LAND"
        assert out["relation_declaration"]["outcome"] == UNDECLARED_PAIR
        assert out["relation_declaration"]["predicate"] == "contains"

    def test_the_operator_door_is_silent_on_a_declared_pair(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_operator("IMPL", api, [db], label="dependsOn")
        assert "relation_declaration" not in out

    def test_the_direct_edge_door_reports(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_direct_edge("IMPL", api, db, label="contains")
        assert out["created"] is True, "the edge must LAND"
        assert out["relation_declaration"]["outcome"] == UNDECLARED_PAIR

    def test_the_structural_doors_refusal_names_its_correction(self, sdk):
        """AX Rule 2 on the door `link_entities` dispatches a domain verb to.

        Measured before this change: `link_entities("blocks", product, feature)`
        raised the bare sentence ``Unknown predicate: blocks`` — the caller
        learned the word was refused and got neither the vocabulary that IS
        accepted nor the door that would record its verb. The third part is
        writable here, so the refusal stays and says both.

        ⛔ It must NOT route the caller back to `link_entities`: that is the
        circular advice, because this method is the arm `link_entities`
        dispatches to.
        """
        product = _obj(sdk, "P", "product-strategy:product")
        feature = _obj(sdk, "F", "product-strategy:feature")
        with pytest.raises(ValueError) as exc:
            sdk.link_entities("blocks", product, feature)
        message = str(exc.value)
        # (2) exactly what failed
        assert "Unknown predicate: blocks" in message
        # (3) exactly what to do — the accepted vocabulary …
        assert "hasPart" in message and "dependsOn" in message
        # … and the doors that record a DOMAIN verb
        assert "create_operator" in message
        assert "create_direct_edge" in message
        assert "label=" in message

    def test_the_direct_edge_door_reads_the_LABEL_not_the_mechanism(self, sdk):
        """`op_type` is Layer 1 (a mechanism), never a declared relation."""
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_direct_edge("IMPL", api, db)
        assert "relation_declaration" not in out, (
            "a write that names no domain verb has no pair premise")


def _edge_exists(sdk, from_id, to_id, predicate) -> bool:
    rows = sdk._get_proj().g.query(
        f"MATCH (a)-[r:{predicate}]->(b) WHERE a.id=$f AND b.id=$t RETURN count(r)",
        params={"f": from_id, "t": to_id}).result_set
    return bool(rows and rows[0][0])


def _edge_label(sdk, from_id, to_id, rel_type="IMPL"):
    """The STORED `label` property on the direct edge between two ids."""
    rows = sdk._get_proj().g.query(
        f"MATCH (a)-[r:{rel_type}]->(b) WHERE a.id=$f AND b.id=$t "
        "RETURN r.label",
        params={"f": from_id, "t": to_id}).result_set
    return rows[0][0] if rows else None


def _operator_label(sdk, operator_id):
    """The STORED `label` property on the operator Point."""
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.label",
        params={"id": operator_id}).result_set
    return rows[0][0] if rows else None


# ══ 6. the 69a9fae6c8 review's findings, one class ═════════════════


class TestTheSpellingIsWrittenWhereItIsReported:
    """Review F3 — `normalise` must be TRUE on every door that reports it.

    `PairVerdict.predicate` is documented as *"the spelling to WRITE: the
    declared one on a `NORMALISED` verdict"* and the module's table says a
    re-spelling is normalised **silently**. Measured at the previous head, only
    `_create_edge` applied it: these two doors reported the write as normalised
    while storing the caller's spelling, so the graph held two spellings of ONE
    declared relation and no reply said so.
    """

    def test_the_direct_edge_door_writes_the_declared_spelling(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_direct_edge("IMPL", api, db, label="DEPENDSON")
        assert out["created"] is True
        assert _edge_label(sdk, api, db) == "dependsOn", (
            "a re-spelling of a DECLARED predicate must be written as the "
            "DECLARED spelling — otherwise the graph accumulates two tokens "
            "for one relation while the reply calls the write normalised")
        # …and it stays SILENT: the outcome is not one of the two REPORTED ones.
        assert "relation_declaration" not in out
        assert "warnings" not in out

    def test_the_operator_door_writes_the_declared_spelling(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_operator("IMPL", api, [db], label="DEPENDSON")
        assert out.get("id"), "the operator must LAND"
        assert _operator_label(sdk, out["id"]) == "dependsOn", (
            "the operator route stored the caller's spelling (review F3)")
        assert "relation_declaration" not in out

    def test_the_predicate_leg_still_warns_about_an_UNDECLARED_label(self, sdk):
        """The other direction: a gate never rewrites what it reports as bad.

        `_canonical_relation_spelling` answers `None` for a verb no pack
        declares, and the caller's own spelling must survive — the `#1934`
        predicate leg's warning names the word the CALLER used.
        """
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_operator("IMPL", api, [db], label="inventedVerb")
        assert _operator_label(sdk, out["id"]) == "inventedVerb"
        assert any(w.get("code") == "undeclared_relation"
                   for w in out.get("warnings") or ()), \
            "the #1934 predicate leg still speaks about an invented verb"


class TestThePairUniquenessIsOverPairs:
    """Review F5 — uniqueness over hit ROWS reports a declared pair as undeclared.

    `declared_relation_pairs()` accumulates rows from EVERY loaded pack, so two
    manifests declaring the same predicate for the same pair produce two hits
    for ONE pair. A `len(hits) == 1` test then answers "no single relation" —
    an agent-facing falsehood about a pair the packs declare twice.
    """

    def test_a_pair_declared_twice_is_still_declared(self):
        pair = ("dev:api", "dev:database")
        declared = {"dependsOn": (pair, pair)}
        verdict = relation_pair_verdict(
            "dependsOn", "dev:api", "dev:database", declared=declared)
        assert verdict.outcome == ACCEPT, (
            f"a pair the packs declare TWICE was reported as {verdict.outcome}")
        assert verdict.payload is None, "an accept says nothing"

    def test_the_reverse_check_is_deduped_the_same_way(self):
        """`REVERSED_PAIR` shares the helper, so it shared the bug."""
        declared = {"dependsOn": (("dev:database", "dev:api"),
                                   ("dev:database", "dev:api"))}
        verdict = relation_pair_verdict(
            "dependsOn", "dev:api", "dev:database", declared=declared)
        assert verdict.outcome == REVERSED_PAIR

    def test_two_DIFFERENT_matching_pairs_still_resolve_to_none(self):
        """Deduping must not turn a genuine ambiguity into a guess."""
        declared = {"dependsOn": (("dev:api", "dev:database"),
                                   ("dev:api", "dev:cache"))}
        # A bare `dev` pair that both declarations' local halves could answer is
        # not expressible here, so assert the surviving rule directly: one
        # asked pair resolves only when exactly ONE distinct pair matches.
        assert relation_pair_verdict(
            "dependsOn", "dev:api", "dev:cache", declared=declared
        ).outcome == ACCEPT
        assert relation_pair_verdict(
            "dependsOn", "dev:cache", "dev:database", declared=declared
        ).outcome == UNDECLARED_PAIR


class TestAReportedTargetIsAttributable:
    """The prior review's P3 — a multi-target write kept only the FIRST fact.

    `relation_declaration` is one dict, so on a multi-target operator write the
    other targets' facts survived only in `warnings` — and without a target id
    there they could not be attributed to the target they are about.
    """

    def test_every_reported_target_carries_its_own_id(self, sdk):
        epic = _obj(sdk, "Epic", "dev:epic")
        issue = _obj(sdk, "Issue", "dev:issue")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_operator("IMPL", epic, [issue, db], label="dependsOn")
        reported = {w["relation_declaration"]["target"]
                    for w in out["warnings"]}
        assert reported == {issue, db}, (
            f"every reported target must be named; got {reported}")
        # the singular field is documented as the FIRST reported target, and it
        # is attributable too rather than being an anonymous block
        assert out["relation_declaration"]["target"] == issue


class TestIngestCarriesTheReport:
    """Review F2 — the bulk surface computed the judgement and dropped it.

    Ingest is `#14263`'s motivating surface (it mints edges in bulk), and it was
    the ONE place where the leg ran and the caller saw nothing: the router read
    `created`/`id` and rebuilt the envelope from scratch.
    """

    def test_the_direct_route_carries_the_declaration(self, sdk):
        out = sdk._ingest(_pair_bundle(reify=False, label="contains"),
                          granularity="granular")
        assert out["relation_declarations"], (
            "the ingest surface paid for the leg and reported NOTHING")
        entry = out["relation_declarations"][0]
        assert entry["outcome"] == UNDECLARED_PAIR
        assert entry["predicate"] == "contains"
        assert entry["connection"] == "c1"
        assert entry["fromId"] and entry["toId"]
        # the per-connection view (granular mode) agrees with the rollup
        conn_result = next(r for r in out["results"]
                           if r["section"] == "connections")["result"]
        assert conn_result["relation_declaration"]["outcome"] == UNDECLARED_PAIR

    def test_the_operator_route_carries_the_declaration(self, sdk):
        out = sdk._ingest(_pair_bundle(reify=True, label="contains"))
        assert out["relation_declarations"]
        assert out["relation_declarations"][0]["outcome"] == UNDECLARED_PAIR

    def test_a_declared_pair_reports_nothing_and_the_key_is_still_there(self, sdk):
        out = sdk._ingest(_pair_bundle(reify=False, label="dependsOn"))
        assert "relation_declarations" in out, (
            "the field is part of the STABLE key set — always present")
        assert out["relation_declarations"] == []

    def test_the_RELATION_route_reports_too(self, sdk):
        """Round-3 review F1 — `{"relation": …}` is a connect door as well.

        That connection is written through the projection primitive, so it
        reaches no connect door and the leg never ran: `hasPart`/`dependsOn`/
        `ownedBy` are all pack-declared AND in `_VALID_EDGE_PREDICATES` (the
        allow-list `_check_connection` requires), so the silence was reachable
        with the shipped catalogs — the exact fail-silent class #14263 exists
        for.
        """
        out = sdk._ingest(_relation_bundle("hasPart"))
        assert out["relation_declarations"], (
            "the relation route wrote the edge and reported NOTHING — the "
            "silence #14263 exists to close")
        entry = out["relation_declarations"][0]
        assert entry["outcome"] == UNDECLARED_PAIR
        assert entry["predicate"] == "hasPart"
        assert entry["from"] == "dev:epic" and entry["to"] == "dev:issue"
        assert entry["connection"] == "c1"
        # …and the EDGE really landed: reported, never refused (#1934).
        refs = out["ids"]["refs"]
        assert _edge_exists(sdk, refs["e1"], refs["e2"], "hasPart")
        # the per-connection view (granular mode) agrees with the rollup
        gran = sdk._ingest(_relation_bundle("hasPart"), granularity="granular")
        conn_result = next(r for r in gran["results"]
                           if r["section"] == "connections")["result"]
        assert conn_result["relation_declaration"]["outcome"] == UNDECLARED_PAIR

    def test_the_ids_descriptor_keeps_its_documented_shape(self, sdk):
        """Round-4 review F2 — the report must not leak into `ids`.

        `_carry_relation_declaration` mutates the connection's result dict IN
        PLACE, and the relation route used to append that same object to
        `ids["connections"]`, so the documented §6.1 descriptor grew
        `relation_declaration` and a `warnings` list of DICTS inside `ids` — a
        key the envelope reserves for key-prefixed strings. The direct route
        keeps its report out of `ids` for exactly this reason.
        """
        out = sdk._ingest(_relation_bundle("hasPart"))
        descriptor = out["ids"]["connections"][0]
        assert set(descriptor) == {"relation", "from", "to", "deduped"}, (
            f"the `ids` descriptor changed shape: {sorted(descriptor)}")
        assert out["relation_declarations"], (
            "…and the report still happened: it belongs in the rollup")

    def test_a_declared_pair_on_the_relation_route_stays_silent(self, sdk):
        """The other direction: the new door must not start inventing reports."""
        out = sdk._ingest(_relation_bundle("dependsOn", from_kind="dev:api",
                                          to_kind="dev:database"))
        assert out["relation_declarations"] == []

    def test_a_reversed_rollup_entry_names_the_JUDGED_ends(self, sdk):
        """Round-3 review F2 — `from`/`to` and `fromId`/`toId` must agree.

        With `direction="<-"` the relation runs target → source, so the
        judgement is about `(target, source)`. Stamping `fromId` from the
        connection's ARGUMENTS paired a database KIND with an API NODE: two
        halves of one entry naming different nodes.
        """
        out = sdk._ingest(_pair_bundle(reify=True, label="dependsOn",
                                       direction="<-"))
        entry = out["relation_declarations"][0]
        assert entry["outcome"] == REVERSED_PAIR, entry
        assert entry["from"] == "dev:database" and entry["to"] == "dev:api"
        refs = out["ids"]["refs"]
        assert entry["fromId"] == refs["oB"], (
            "`fromId` must be the node `from` names (the connection's `to`)")
        assert entry["toId"] == refs["oA"]
        assert entry["direction"] == "<-"


class TestTheContractExampleIsTheEmittedPayload:
    """Review F2 — the documented `rule` named a field the payload does not carry.

    `INGEST_CONTRACT.md` §3.3 is a CLIENT's only source for this block, and its
    example said `"rule": "undeclared_relation_pair"` — that is the
    `warnings[].code` (`RG.warning_shape`), a different field on a different
    object — while the shipped payload carries `relation-pair-not-declared`
    (`RULE_UNDECLARED`). The same example also listed the JUDGED pair under
    `declaredFor` beside `outcome: undeclared_pair`, so it contradicted itself
    about the one thing it was illustrating. Nothing compared the two, so the
    contract could not go red; this does — the doc example is now parsed and
    compared, field by field, to a payload the code emits for those kinds.
    """

    #: Everything in the block that is a FACT about the verdict. The id fields
    #: (`target`/`connection`/`fromId`/`toId`) are per-write and are shown in the
    #: doc as placeholders on purpose, so they are not compared.
    #: ⛔ `direction` is IN the compared set (round-4 review, F1): it became a
    #: payload field on the doors that resolve a direction, and an allow-list
    #: that did not name it would have watched the doc gain a field-blind pin —
    #: which is exactly what happened before this round.
    STABLE = ("outcome", "predicate", "from", "to", "rule", "direction",
              "declaredFor", "declaredBetweenTheseKinds", "override", "undo")

    @staticmethod
    def _documented_example() -> dict:
        doc = (Path(__file__).resolve().parent.parent
               / "docs" / "INGEST_CONTRACT.md").read_text(encoding="utf-8")
        section = doc.split("### 3.3", 1)[1]
        block = section.split("```json", 1)[1].split("```", 1)[0]
        return json.loads(block)

    def test_the_example_is_a_payload_the_code_emits(self, sdk):
        documented = self._documented_example()
        out = sdk._ingest(_pair_bundle(reify=False, label="assignedTo"))
        emitted = out["relation_declarations"][0]
        assert {k: documented[k] for k in self.STABLE} == {
            k: emitted[k] for k in self.STABLE}, (
            "INGEST_CONTRACT.md §3.3 drifted from the payload it documents")
        # the compared set must not silently fall behind the payload: a field the
        # code emits that the pin does not name is a doc gap this test cannot see
        # (round-4 review, F1 — `direction` was added and the pin stayed green).
        assert set(self.STABLE) <= set(emitted), (
            "this test compares a field the payload does not carry: "
            f"{sorted(set(self.STABLE) - set(emitted))}")

    def test_the_example_publishes_the_RULE_id_and_not_the_warning_CODE(self):
        documented = self._documented_example()
        assert documented["rule"] == rg.RULE_UNDECLARED
        assert documented["rule"] != "undeclared_relation_pair", (
            "that spelling is warnings[].code, not this field's value")
        # and the message's factual core is the pair it actually judged
        assert documented["message"].startswith(
            f"relation '{documented['predicate']}' is not declared between "
            f"{documented['from']} and {documented['to']}")


class TestAResubmissionOfAReSpellingDedupes:
    """Review F1 — the dedup KEY read the caller's spelling; the writers STORE the declared one.

    MEASURED at the head this review left (`86590a4e86`), on this exact fixture
    and an embedded graph, before the fix: bundle #1 with `label="DEPENDSON"`
    wrote ONE operator labelled `dependsOn`; the IDENTICAL bundle #2 minted a
    SECOND operator carrying the SAME canonical label — two live `IMPL` factors
    over one pair, EP pricing the belief twice. That is the `#9750` double-count
    class `_find_operator`'s own comment says this key exists to prevent, and
    the part/whole route reached it too (`composedOf` is outside
    `CORE_PREDICATES`, so the `#12067` guard does not even raise first). The
    control run with `label="dependsOn"` deduped onto ONE operator and returned
    the same id, so the spelling is the only variable.

    Why the tests are here rather than in a docstring: the door tests above pin
    what a write REPORTS, and this pins what a RETRY finds — the property that
    makes ingest idempotent (`INGEST_CONTRACT` §15's `ids` equality across a
    resubmission).
    """

    @staticmethod
    def _operators_over(sdk, op_type, ids):
        """The operators of ``op_type`` that carry BOTH ``ids`` — scope-bounded.

        ⛔ NOT "every operator of this type in the graph": on a lane that sets
        `TORTOISE_DB_URI` these tests share one container graph (the epic-#1647
        redirect), so an absolute count reads operators minted by OTHER tests
        and the pin fails for a reason that has nothing to do with this one.
        Bounding the read to the two endpoint ids this test just minted keeps
        the assertion about THIS pair on every lane.
        """
        # the STORAGE rel type, mapped exactly as the writer writes it
        # (`MECHANISM_REL_TYPES`: a `composedOf` leg is stored as `hasPart`)
        from tortoise.ep import MECHANISM_REL_TYPES

        rel = MECHANISM_REL_TYPES.get(op_type, "hasPart")
        rows = sdk._get_proj().g.query(
            f"MATCH (o:Point {{is_operator:true, op_type:$t}})-[:{rel}]->(n) "
            "WHERE n.id IN $ids WITH o, count(DISTINCT n.id) AS ends "
            "WHERE ends = size($ids) RETURN o.id, o.label",
            params={"t": op_type, "ids": list(ids)}).result_set
        return [(row[0], row[1]) for row in rows]

    def test_the_operator_route_dedupes_a_re_spelled_resubmission(self, sdk):
        bundle = _pair_bundle(reify=True, label="DEPENDSON")
        first = sdk._ingest(bundle)
        second = sdk._ingest(bundle)
        assert second["ids"]["connections"] == first["ids"]["connections"], (
            "a resubmission of the SAME bundle must return the operator it "
            f"already wrote; got {second['ids']['connections']} then "
            f"{first['ids']['connections']}")
        assert second["deduped"]["connections"] == 1
        assert second["created"]["connections"] == 0
        pair = [first["ids"]["refs"]["oA"], first["ids"]["refs"]["oB"]]
        operators = self._operators_over(sdk, "IMPL", pair)
        assert len(operators) == 1, (
            "one pair must never carry two live mechanisms (#9750): "
            f"{operators}")
        # the KEY agreeing with the writer is the whole point: pass 1 stored the
        # DECLARED spelling, so pass 2 had to ask for that same token
        assert operators[0][1] == "dependsOn"

    def test_the_part_whole_route_does_not_mint_a_second_operator(self, sdk):
        """The route where a key miss MINTS instead of hitting the #12067 guard."""
        bundle = _pair_bundle(reify=True, label="CONTAINS", operator="composedOf")
        first = sdk._ingest(bundle)
        second = sdk._ingest(bundle)
        operators = self._operators_over(
            sdk, "composedOf",
            [first["ids"]["refs"]["oA"], first["ids"]["refs"]["oB"]])
        assert len(operators) == 1, (
            "the resubmission minted a duplicate part/whole operator: "
            f"{operators}")
        assert second["ids"]["connections"] == first["ids"]["connections"]
        assert operators[0][1] == "contains"

    def test_the_control_a_canonical_label_already_deduped(self, sdk):
        """The property the re-spelling lost — green BEFORE the fix, so the two
        above are red for the spelling and for nothing else.\n        """
        bundle = _pair_bundle(reify=True, label="dependsOn")
        first = sdk._ingest(bundle)
        second = sdk._ingest(bundle)
        assert second["ids"]["connections"] == first["ids"]["connections"]
        assert len(self._operators_over(
            sdk, "IMPL",
            [first["ids"]["refs"]["oA"], first["ids"]["refs"]["oB"]])) == 1

    def test_a_LEGACY_operator_stored_under_the_raw_spelling_is_still_found(self, sdk):
        """The SECOND probe (round-3 review, VGATE F-3): pre-resolution graphs.

        A build that predates the doors' spelling resolution stored the CALLER's
        token verbatim, so that operator is keyed in a spelling `_ingest` has
        just replaced. Asking only for the declared one would turn this retry —
        which exact-hit before the fix — into the very miss F1 is about.
        """
        from tortoise.projection import _live_write_scope

        bundle = _pair_bundle(reify=True, label="DEPENDSON")
        first = sdk._ingest(bundle)
        oid = first["ids"]["connections"][0]
        # Re-create the pre-resolution stored shape: the row carries the RAW token.
        with _live_write_scope("test-legacy-spelling-14263"):
            sdk._get_proj().g.query(
                "MATCH (o:Point {id:$id}) SET o.label = 'DEPENDSON'",
                params={"id": oid})
        second = sdk._ingest(bundle)
        assert second["ids"]["connections"] == [oid], (
            "a legacy operator keyed under the caller's spelling must still be "
            "adopted — this retry exact-hit it before the fix")
        assert len(self._operators_over(
            sdk, "IMPL",
            [first["ids"]["refs"]["oA"], first["ids"]["refs"]["oB"]])) == 1, (
            "the legacy arm minted a duplicate instead of adopting the row")


class TestTheJudgedPairFollowsTheDeclaredDirection:
    """A reversed DECLARATION must not be read as a reversed WRITE.

    Measured before the fix, at the head the review left `UNVERIFIED`:

        _create_operator("IMPL", db, [api], label="dependsOn", direction="<-")
        -> outcome == "reversed_pair"   (direction="<-", basis="explicit")

    `dependsOn` is declared exactly `dev:api -> dev:database`, and this write
    IS that orientation — `direction="<-"` says the relation runs target ->
    source, i.e. api -> db. So the reply told the caller the pack declares only
    the reverse of the pair it had in fact declared.
    """

    def test_a_declared_reverse_direction_is_not_reported_as_reversed(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_operator("IMPL", db, [api], label="dependsOn",
                                    direction="<-")
        assert out.get("direction") == "<-"
        assert "relation_declaration" not in out, (
            "the relation runs api -> db, which the pack DOES declare — this "
            f"write must be silent, got {out.get('relation_declaration')}")

    def test_a_forward_write_of_a_declared_pair_stays_silent(self, sdk):
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        out = sdk._create_operator("IMPL", api, [db], label="dependsOn")
        assert "relation_declaration" not in out

    def test_the_wrong_way_round_is_still_REPORTED(self, sdk):
        """The pin must not become "never report again": a real reversal is."""
        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        # forward declaration on the reversed pair -> the pack declares the
        # reverse, so this IS the reported state
        out = sdk._create_operator("IMPL", db, [api], label="dependsOn")
        assert out["relation_declaration"]["outcome"] == REVERSED_PAIR


def _pair_bundle(*, reify: bool, label: str, operator: str = "IMPL",
                 direction: str | None = None) -> dict:
    """Two Object endpoints of DECLARED kinds, and one connection by label.

    `dev:api` / `dev:database` occur in the declared vocabulary (as
    `dependsOn`'s pair) but NOT as `contains`'s, so the pair leg reaches a
    verdict instead of abstaining on an unknown scope. `reify` picks the route:
    without it an Object->Object `IMPL` is an operator-less DIRECT edge, with it
    the operator route — so the two tests drive both writers. `operator` selects
    the mechanism, so a resubmission test can drive a NON-mechanism op_type (the
    case where a key miss MINTS rather than raising, #9750).
    """
    conn = {"ref": "c1", "from": "oA", "to": "oB",
            "operator": operator, "label": label}
    if reify:
        conn["reify"] = True
    if direction is not None:
        conn["direction"] = direction
    return {
        "entities": [
            {"ref": "oA", "type": "object", "name": "The API spec",
             "objectKind": "dev:api"},
            {"ref": "oB", "type": "object", "name": "The database",
             "objectKind": "dev:database"},
        ],
        "connections": [conn],
    }


def _relation_bundle(predicate: str, *, from_kind: str = "dev:epic",
                     to_kind: str = "dev:issue") -> dict:
    """Two Object endpoints of DECLARED kinds joined by a `{"relation": …}`
    connection — the route that writes through the projection primitive.

    `hasPart`/`dependsOn`/`ownedBy` are BOTH pack-declared and in
    `_VALID_EDGE_PREDICATES` (the allow-list `_check_connection` requires), so
    this route really lands the edge. The default kinds (`dev:epic`,
    `dev:issue`) are declared kinds but NOT `hasPart`'s declared pair
    (`dev:theme -> dev:epic`), so the leg reaches an UNDECLARED verdict; the
    declared case passes `dev:api`/`dev:database` (the `dependsOn` pair).
    """
    return {
        "entities": [
            {"ref": "e1", "type": "object", "name": "The epic",
             "objectKind": from_kind},
            {"ref": "e2", "type": "object", "name": "The issue",
             "objectKind": to_kind},
        ],
        "connections": [{"ref": "c1", "from": "e1", "to": "e2",
                         "relation": predicate}],
    }


# ══ 6. cost, and no surface change ════════════════════════════════════════


class TestCostAndSurface:
    def test_zero_model_calls_on_the_guard_path(self, sdk, monkeypatch):
        """A guard that needs a model is a different feature.

        ⛔ The seams are ASSERTED to exist before they are patched — a first
        draft of this pattern patched seam names that did not exist, so two
        thirds of the test silently did nothing.
        """
        import tortoise.embeddings as embeddings_mod
        import tortoise.extractor_v2 as ext

        def _no_model(*_a, **_kw):  # pragma: no cover - only fires on failure
            raise AssertionError("the relation-pair guard issued a model call")

        # ⛔ ASSERTED PRESENT before patching: a `hasattr`-guarded patch is a
        # test that silently does nothing when the seam is renamed.
        assert hasattr(ext, "_rollup_llm"), "the extraction LLM seam moved"
        assert hasattr(embeddings_mod, "EmbeddingModel"), "the embedding seam moved"
        monkeypatch.setattr(ext, "_rollup_llm", _no_model)
        monkeypatch.setattr(
            embeddings_mod.EmbeddingModel, "get", staticmethod(_no_model),
            raising=False)

        api = _obj(sdk, "API", "dev:api")
        db = _obj(sdk, "DB", "dev:database")
        epic = _obj(sdk, "Epic", "dev:epic")
        for call in (
            lambda: sdk.link_entities("dependsOn", api, db),
            lambda: sdk.link_entities("dependsOn", epic, db),
            lambda: sdk._create_direct_edge("IMPL", api, db, label="contains"),
        ):
            call()

    def test_the_new_sdk_helpers_are_private(self):
        """No MCP tool, no public SDK method — a surface change needs approval.

        The repo rule (`AGENTS.md`): adding or removing a tool/method is a
        surface change; changing what an existing door VALIDATES is not. Both
        helpers this PR adds are underscore-prefixed, and neither appears in the
        tool registry.
        """
        from tortoise.tool_registry import TOOL_REGISTRY

        for name in ("_relation_pair_notes", "_endpoint_kind_map"):
            assert hasattr(TortoiseSDK, name)
        names = {getattr(t, "name", None) for t in TOOL_REGISTRY}
        assert not any("relation_pair" in str(n) for n in names)
        assert not any("declaration" in str(n) for n in names)
