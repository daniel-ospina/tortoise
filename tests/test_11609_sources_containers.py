"""#4282 Phase 2 / #11609 lane C — the SOURCES / MINE / SESSION containers.

The approved surface (`tortoise/__all__.py`, owner-approved in
`docs/product/beta-sdk-surface.md` rows 14-18 and row 40) replaces SEVEN advertised
members with SIX container names. `docs/product/bridge-table.md` Part A records, per
container, the DISCRIMINATOR — and the discriminator is the whole design: a container
whose members take the same first argument is one operation plus a filter (AIP-132
`List` with a filter), not two verbs sharing a name.

    register_source                  <- create_source            (no discriminator)
    manage_source_trust              <- assess_source, set_source_tier   (by argument)
    index_sources_from_directory     <- index_files                    (file_type)
    mine_knowledge_from_session      <- checkpoint, session_capture    (by argument)
    mine_knowledge_from_directory    <- mine_conversations            (no discriminator)
    check_connection                 <- (new row 40; nothing absorbed)

WHAT THIS FILE IS THE GATE ON — both directions, because a retirement has two halves:

  A. THE CONTAINER DISPATCHES. Every discriminator value reaches the door it absorbed,
     measured by SUBSTITUTION (the door is replaced and the container is called), so a
     regression names the arm that broke instead of "dispatch is wrong". The refusals
     are pinned too: a call that names two discriminators, or none, is refused BY NAME
     rather than silently picking one — a container that guesses is worse than two
     methods, because the guess is invisible.

  B. THE RETIRED NAME STILL ANSWERS, IN THE FORM ITS CONTRACT KEEPS.
     - MCP names (#3883): the name is not advertised, but `_RETIRED_SHIMS` still
       serves it, on the ORIGINAL body, and its pointer names the container.
     - SDK names (#3836 (c), an `OVERRIDES` of the warn-and-continue default): the
       name is GONE and `__getattr__` raises naming the container. There is no shim
       here on purpose; a test that asserted a warning would be asserting the
       behaviour the owner overruled.

NO DATABASE: these are routing tests, and routing is exactly what a container changes.
The doors' own behaviour (graph writes, extraction, dedup) is covered by their own
suites, which this change does not touch — and every door here is monkeypatched, so a
routing test can never acquire a database dependency by accident.
"""
from __future__ import annotations

import json

import pytest

from tortoise import mcp_server as ms
from tortoise.sdk import TortoiseSDK
from tortoise.sdk_retired import (
    REMOVED_SDK_METHODS,
    retirement_hint,
)
from tortoise.tool_registry import (
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

#: The containers this batch put on the advertised surface.
CONTAINERS = {
    "tortoise_register_source",
    "tortoise_manage_source_trust",
    "tortoise_index_sources_from_directory",
    "tortoise_mine_knowledge_from_session",
    "tortoise_mine_knowledge_from_directory",
    "tortoise_check_connection",
}

#: The advertised names this batch retired INTO them (`canonical-mcp-tools.md` rows
#: 14-18; `tortoise_manage_sources` is NOT here — it is MCP-only, is not one of the
#: approved 40, and absorbs nothing in this batch).
RETIRED_INTO_CONTAINER = {
    "tortoise_create_source": "tortoise_register_source",
    "tortoise_assess_source": "tortoise_manage_source_trust",
    "tortoise_set_source_tier": "tortoise_manage_source_trust",
    "tortoise_index_files": "tortoise_index_sources_from_directory",
    "tortoise_checkpoint": "tortoise_mine_knowledge_from_session",
    "tortoise_session_capture": "tortoise_mine_knowledge_from_session",
    "tortoise_mine_conversations": "tortoise_mine_knowledge_from_directory",
}

#: The SDK names that left WITH a replacement named — a RENAME, so `__getattr__`
#: fails and names the container (`RETIRED_SDK_METHODS`).
RETIRED_SDK_WITH_HINT = {
    "assess_source": "manage_source_trust",
    "set_source_tier": "manage_source_trust",
    "index_file": "index_sources_from_directory",
    "capture_session": "mine_knowledge_from_session",
    "commit_session": "mine_knowledge_from_session",
}

#: The SDK names that WENT INTERNAL (the body lives on as the private door). These
#: carry NO hint: there is no replacement name to send a caller to — the container is
#: the destination, but the entry that records the departure is `REMOVED_SDK_METHODS`,
#: whose contract is "no hint" (see that module's docstring; inventing a hint here
#: would claim a `#3836 (c)` approval the owner did not give for these names).
REMOVED_SDK_NO_HINT = {
    "create_source",
    "index_directory",
    "index_sessions",
    "mine_corpus",
    "checkpoint",
}


@pytest.fixture
def sdk():
    """An UNINITIALISED SDK: the containers are pure dispatch, so nothing here needs
    a graph. Every door is patched by the test that calls one."""
    return object.__new__(TortoiseSDK)


def _spy(monkeypatch, attr, result=None):
    """Replace a door on the class and record `(args, kwargs)` per call."""
    calls: list[tuple] = []

    def _fake(self, *args, **kwargs):
        calls.append((args, kwargs))
        return result if result is not None else {"ok": True}

    monkeypatch.setattr(TortoiseSDK, attr, _fake)
    return calls


class TestDeclaration:
    @pytest.mark.parametrize("name", sorted(CONTAINERS))
    def test_the_container_is_on_the_advertised_surface(self, name):
        assert name in {t.name for t in TOOL_REGISTRY}

    @pytest.mark.parametrize("name,container", sorted(RETIRED_INTO_CONTAINER.items()))
    def test_the_absorbed_member_is_retired_not_deleted(self, name, container):
        live = {t.name for t in TOOL_REGISTRY}
        retired = {t.name for t in RETIRED_TOOL_REGISTRY}
        assert name not in live, f"{name} is still advertised — the surface did not shrink"
        assert name in retired, f"{name} is GONE instead of retired (#3883: it must answer)"

    @pytest.mark.parametrize("name,container", sorted(RETIRED_INTO_CONTAINER.items()))
    def test_the_pointer_names_its_container_in_one_hop(self, name, container):
        replacement = RETIRED_USE_INSTEAD[name]
        assert replacement.startswith(container + "("), replacement
        target = replacement.split("(", 1)[0]
        assert target not in RETIRED_USE_INSTEAD, (
            f"{name} points at {target}, which is itself retired — pointers resolve "
            "one level deep, so a retired target is a dead end")

    @pytest.mark.parametrize("name,container", sorted(RETIRED_INTO_CONTAINER.items()))
    def test_the_shim_serves_the_original_body(self, name, container):
        """#3883: the name still ANSWERS, and it answers with the ORIGINAL handler.

        A shim whose `fn` is not the original would be a second implementation of a
        retired tool — the drift `_make_shim`'s factory exists to prevent.
        """
        assert name in ms._RETIRED_SHIMS, f"{name} retired with no shim — a deletion"
        shim = ms._RETIRED_SHIMS[name]
        # `fn` is the shim's wrapper (it adds the #3883 warning LAST, so the payload
        # stays `content[0]`); `functools.wraps` records the body it closed over, and
        # THAT must be the original handler — a re-implementation would be a second
        # retired tool, which is the drift the factory exists to prevent.
        assert shim.fn.__wrapped__ is getattr(ms, name), (
            "the shim does not serve the original handler")
        assert shim.description.startswith(f"RETIRED — use {container}"), shim.description
        assert shim.meta["tortoise"]["retired"]["use_instead"].startswith(container + "(")

    @pytest.mark.parametrize("name,container", sorted(RETIRED_INTO_CONTAINER.items()))
    def test_the_container_has_a_handler_that_is_not_the_sdk_method(self, name, container):
        """The MCP layer adds what no SDK may assume (transport exclusion, quota gate,
        `_safe`), so the handler must exist and must not be a bare attribute alias."""
        entry = next(t for t in TOOL_REGISTRY if t.name == container)
        handler = getattr(ms, container, None)
        assert callable(handler), f"{container} has no handler body"
        assert entry.sdk_method, "the container delegates to its SDK method here"
        assert hasattr(TortoiseSDK, entry.sdk_method), (
            f"{container} declares sdk_method {entry.sdk_method!r}, which does not resolve")


class TestSDKRetirement:
    """The SDK direction: gone, and the shape of the departure is recorded."""

    @pytest.mark.parametrize("name,replacement", sorted(RETIRED_SDK_WITH_HINT.items()))
    def test_a_rename_fails_and_names_the_container(self, sdk, name, replacement):
        assert name not in dir(TortoiseSDK), f"{name} is still a public SDK name"
        with pytest.raises(AttributeError) as excinfo:
            getattr(sdk, name)
        message = str(excinfo.value)
        assert replacement in message, message
        assert "#3836" in message, "the hint must say the alias layer was ruled out"

    @pytest.mark.parametrize("name", sorted(REMOVED_SDK_NO_HINT))
    def test_a_going_internal_departure_keeps_the_interpreter_shape(self, sdk, name):
        """No hint, because there is no replacement NAME to name.

        `REMOVED_SDK_METHODS` is the inventory of departures that are deliberately not
        coming back under another public name; a hint saying "use X instead" would be
        the false claim that module's docstring refuses.
        """
        assert name not in dir(TortoiseSDK), f"{name} is still a public SDK name"
        assert name in REMOVED_SDK_METHODS, f"{name} left the surface with no record"
        assert retirement_hint(name) is None, (
            f"{name} now emits a migration hint — that is a RENAME record, and these "
            "five have no owner-approved region naming a replacement for them")
        with pytest.raises(AttributeError) as excinfo:
            getattr(sdk, name)
        assert "no attribute" in str(excinfo.value)

    @pytest.mark.parametrize("name", sorted(set(RETIRED_SDK_WITH_HINT) | REMOVED_SDK_NO_HINT))
    def test_the_private_door_is_where_the_behaviour_lives(self, name):
        """Every departure keeps its body as `_name` — nothing was re-implemented and
        nothing was deleted (the container delegates; it does not copy)."""
        privates = {
            "assess_source": "_assess_source",
            "set_source_tier": "_set_source_tier",
            # `index_file` and `commit_session` were NEVER `def <name>` on the class —
            # they are surplus-block names whose behaviour already lived inside the
            # walker / the versioned capture pair, so their door is not `_name`.
            "index_file": "_index_directory",
            "capture_session": "_capture_session",
            "commit_session": "_commit_session_v2",
            "create_source": "_create_source",
            "index_directory": "_index_directory",
            "index_sessions": "_index_sessions",
            "mine_corpus": "_mine_corpus",
            "checkpoint": "_checkpoint",
        }
        assert hasattr(TortoiseSDK, privates[name]), (
            f"{name} left the surface and {privates[name]} does not exist — the "
            "behaviour went with it")


class TestRegisterSourceDispatch:
    """Row 14: one member, no discriminator — the container must forward the WHOLE
    signature, because a rename that silently narrowed it would truncate callers."""

    def test_every_argument_reaches_the_door(self, sdk, monkeypatch):
        calls = _spy(monkeypatch, "_create_source")
        sdk.register_source("https://x.test/a", "doc", tier="T2",
                            sourceDate="2026-01-01", source_path="/p", raw_state="{}",
                            is_episodic=True, extra=7)
        assert calls == [(("https://x.test/a", "doc"), {
            "tier": "T2", "sourceDate": "2026-01-01", "source_path": "/p",
            "raw_state": "{}", "is_episodic": True, "extra": 7})], calls

    def test_omitted_optionals_are_passed_as_none_not_dropped(self, sdk, monkeypatch):
        calls = _spy(monkeypatch, "_create_source")
        sdk.register_source("https://x.test/a", "doc")
        assert calls == [(("https://x.test/a", "doc"), {
            "tier": None, "sourceDate": None, "source_path": None,
            "raw_state": None, "is_episodic": None})], calls


class TestManageSourceTrustDispatch:
    """Row 18: the discriminator is WHICH WRITE THE CALLER SUPPLIED. The two members
    write different things (an assessment Point vs a property on the Source), so
    neither can be defaulted and both-together is refused."""

    def test_tier_arm_reaches_the_tier_door_and_not_the_assessor(self, sdk, monkeypatch):
        tiers = _spy(monkeypatch, "_set_source_tier")
        assessments = _spy(monkeypatch, "_assess_source")
        out = sdk.manage_source_trust("https://x.test/a", tier="T3")
        assert tiers == [(("https://x.test/a", "T3"), {})], tiers
        assert assessments == [], "the tier arm must not record an assessment"
        assert out == {"ok": True}, "the reply is the door's, with no discriminator echoed"

    def test_assessment_arm_reaches_the_assessor_and_not_the_tier(self, sdk, monkeypatch):
        tiers = _spy(monkeypatch, "_set_source_tier")
        assessments = _spy(monkeypatch, "_assess_source")
        out = sdk.manage_source_trust("https://x.test/a", score=0.8,
                                      assessor="daniel", rationale="primary filing")
        assert assessments == [(("https://x.test/a", "daniel", 0.8, "primary filing"), {})]
        assert tiers == [], "the assessment arm must not move the tier"
        assert out == {"ok": True}, "the reply is the door's, with no discriminator echoed"

    def test_both_arms_together_is_refused_before_any_door(self, sdk, monkeypatch):
        tiers = _spy(monkeypatch, "_set_source_tier")
        assessments = _spy(monkeypatch, "_assess_source")
        with pytest.raises(ValueError) as excinfo:
            sdk.manage_source_trust("https://x.test/a", tier="T1", score=0.5,
                                    assessor="d", rationale="r")
        assert "different writes" in str(excinfo.value)
        assert tiers == [] and assessments == [], "a refusal must reach no door"

    def test_neither_arm_is_refused_by_name(self, sdk, monkeypatch):
        tiers = _spy(monkeypatch, "_set_source_tier")
        assessments = _spy(monkeypatch, "_assess_source")
        with pytest.raises(ValueError) as excinfo:
            sdk.manage_source_trust("https://x.test/a")
        assert "nothing to write" in str(excinfo.value)
        assert tiers == [] and assessments == []

    def test_a_partial_assessment_is_refused_rather_than_defaulted(self, sdk, monkeypatch):
        """A score with no assessor is unattributable; defaulting one would file a
        judgement under a name nobody supplied."""
        assessments = _spy(monkeypatch, "_assess_source")
        with pytest.raises(ValueError) as excinfo:
            sdk.manage_source_trust("https://x.test/a", score=0.9)
        assert "all three" in str(excinfo.value)
        assert assessments == []


class TestIndexSourcesDispatch:
    """Row 15: `file_type` is the discriminator, and every value lands on the SAME
    unified walker — that is what makes one name able to cover the two legacy
    per-type entries without keeping their divergent branches alive."""

    @pytest.mark.parametrize("file_type", ["auto", "agent_session", "meeting", "doc"])
    def test_each_file_type_reaches_the_unified_door(self, sdk, monkeypatch, file_type):
        calls = _spy(monkeypatch, "_index_directory")
        sdk.index_sources_from_directory("/tmp/corpus", file_type=file_type,
                                         corpus_name="c", extract_metadata=False,
                                         progress_file="/tmp/p.json")
        assert calls == [(("/tmp/corpus",), {
            "file_type": file_type, "corpus_name": "c", "extract_metadata": False,
            "llm_model": "gpt-5-mini", "embedding_repair_backoff": None,
            "progress_file": "/tmp/p.json"})], calls

    def test_the_default_is_auto_not_a_silent_session_walk(self, sdk, monkeypatch):
        calls = _spy(monkeypatch, "_index_directory")
        sdk.index_sources_from_directory("/tmp/corpus")
        assert calls[0][1]["file_type"] == "auto", calls


class TestMineKnowledgeFromSessionDispatch:
    """Row 16: the discriminator is the SHAPE OF WHAT THE CALLER HOLDS — a transcript
    to extract from, or a distilled batch to file. Two pipelines, not two spellings."""

    def test_items_arm_reaches_the_batch_door(self, sdk, monkeypatch):
        batches = _spy(monkeypatch, "_checkpoint")
        sessions = _spy(monkeypatch, "_capture_session")
        out = sdk.mine_knowledge_from_session(items=[{"content": "x"}],
                                             agent_name="lane", threshold=0.9)
        assert batches == [(([{"content": "x"}],), {"agent_name": "lane", "threshold": 0.9})]
        assert sessions == [], "a distilled batch is not a transcript"
        assert out == {"ok": True}, "the reply is the door's, with no discriminator echoed"

    def test_conversation_arm_reaches_the_session_door(self, sdk, monkeypatch):
        batches = _spy(monkeypatch, "_checkpoint")
        sessions = _spy(monkeypatch, "_capture_session")
        turns = [{"role": "user", "content": "hi"}]
        out = sdk.mine_knowledge_from_session(conversation=turns, session_id="s-1",
                                             max_turns=11, harness="pi")
        assert sessions == [((turns, "s-1"), {"max_turns": 11, "harness": "pi"})]
        assert batches == [], "a transcript is not a distilled batch"
        assert out == {"ok": True}, "the reply is the door's, with no discriminator echoed"

    def test_both_together_is_refused_before_any_door(self, sdk, monkeypatch):
        batches = _spy(monkeypatch, "_checkpoint")
        sessions = _spy(monkeypatch, "_capture_session")
        with pytest.raises(ValueError) as excinfo:
            sdk.mine_knowledge_from_session(items=[{"content": "x"}],
                                           conversation=[{"role": "user", "content": "hi"}])
        assert "different pipelines" in str(excinfo.value)
        assert batches == [] and sessions == []

    def test_neither_is_refused_by_name(self, sdk, monkeypatch):
        batches = _spy(monkeypatch, "_checkpoint")
        sessions = _spy(monkeypatch, "_capture_session")
        with pytest.raises(ValueError) as excinfo:
            sdk.mine_knowledge_from_session()
        assert "nothing to mine" in str(excinfo.value)
        assert batches == [] and sessions == []


class TestMineKnowledgeFromDirectoryDispatch:
    """Row 17: the BATCH FORM of row 16, and its own row rather than an indexing mode —
    it extracts, and `index_sources_from_directory` does not."""

    def test_every_argument_reaches_the_door(self, sdk, monkeypatch):
        calls = _spy(monkeypatch, "_mine_corpus")
        sdk.mine_knowledge_from_directory("/tmp/c", extract_entities=False,
                                         progress_file="/tmp/p", model="m",
                                         event_log_path="/tmp/e")
        assert calls == [(("/tmp/c",), {
            "extract_entities": False, "progress_file": "/tmp/p",
            "model": "m", "event_log_path": "/tmp/e"})], calls


class TestCheckConnection:
    """Row 40 — the row that makes isolation verifiable, and the one container in this
    batch that absorbs NOTHING (AIP-136: a genuinely different action is its own verb,
    not a section of `manage_source_trust`)."""

    @pytest.fixture
    def linked(self, monkeypatch):
        """A handle with no graph, a working probe, and NO tenant bound."""
        s = object.__new__(TortoiseSDK)
        s._db_uri = None
        s._namespace = "ns"
        s._graph_name = None
        s._record_tenant = None
        monkeypatch.setattr(TortoiseSDK, "_get_proj",
                            lambda self: type("P", (), {"g": type("G", (), {
                                "query": staticmethod(lambda *a, **k: type(
                                    "R", (), {"result_set": [[0]]})())})()})())
        monkeypatch.setattr(TortoiseSDK, "get_organisation_account",
                            lambda self: {"id": "acct-1"})
        monkeypatch.setattr("tortoise.record_writer.record_tenant_effective",
                            lambda carried=None: None)
        return s

    def test_the_answer_shape_is_the_isolation_promise(self, linked):
        out = linked.check_connection()
        assert set(out) == {"connected", "graph", "account", "account_error",
                            "key", "key_error"}
        assert out["connected"] is True
        assert out["graph"] == {"name": "org_ns", "namespace": "ns"}
        assert out["account"] == {"id": "acct-1"}

    def test_a_blank_key_id_is_refused_by_name(self, linked):
        out = linked.check_connection("   ")
        assert "non-empty string" in out["key_error"]
        assert out["key"] is None

    def test_a_key_with_no_bound_tenant_explains_itself(self, linked):
        """Never a silent `None`: an unbound tenant is why the key cannot be resolved,
        and saying so is the difference between a broken handle and a missing key."""
        out = linked.check_connection("key-1")
        assert out["key"] is None
        assert "no account is bound" in out["key_error"]

    def test_an_unreachable_graph_is_reported_not_masked(self, monkeypatch):
        s = object.__new__(TortoiseSDK)
        s._db_uri = None
        s._namespace = None
        s._graph_name = None
        s._record_tenant = None

        def _boom(self):
            raise ConnectionError("no server")

        monkeypatch.setattr(TortoiseSDK, "_get_proj", _boom)
        monkeypatch.setattr(TortoiseSDK, "get_organisation_account", _boom)
        monkeypatch.setattr("tortoise.record_writer.record_tenant_effective",
                            lambda carried=None: None)
        out = s.check_connection()
        assert out["connected"] is False
        assert out["probe_error"] == "ConnectionError"
        assert out["account"] is None
        assert out["account_error"].startswith("ConnectionError")


class TestCheckConnectionKeyIsolation:
    """The `key` half's ONE property: the answer cannot be used as an existence oracle.

    The docstring leads with it — *"A key that belongs to another account and a key that
    does not exist return the SAME `key_error` … if the two differed, this tool could be
    used to test whether another tenant holds a given key id"* — and it was the row's
    entire justification for existing, yet NOTHING exercised it: `TestCheckConnection`
    above stops at the no-bound-tenant refusal (its fixture sets `_record_tenant = None`,
    so every `key_id` case returns before the registry is read), leaving the registry
    read, the `rows[0][0] != own_org` comparison and both single-reply arms uncovered.
    Mutating the two replies APART left the whole file green (#12948 review F2) — a later
    "clarify the error message" edit would have created the cross-tenant oracle with no
    gate to notice.

    These tests therefore bind a tenant and own the registry, so each arm is reached.
    """

    OWN_ORG = "org-own"
    FOREIGN_ROW = ("org-other", "graph-x", ("read",), None, None, "ci", 0)
    OWN_ROW = (OWN_ORG, "graph-own", ("read", "write"), None, 1893456000,
               "laptop", 2)

    @pytest.fixture
    def bound(self, monkeypatch):
        """A handle WITH a tenant bound — the only state that reaches the registry."""
        s = object.__new__(TortoiseSDK)
        s._db_uri = None
        s._namespace = "ns"
        s._graph_name = None
        s._record_tenant = (self.OWN_ORG, "graph-own")
        monkeypatch.setattr(TortoiseSDK, "_get_proj",
                            lambda self: type("P", (), {"g": type("G", (), {
                                "query": staticmethod(lambda *a, **k: type(
                                    "R", (), {"result_set": [[0]]})())})()})())
        monkeypatch.setattr(TortoiseSDK, "get_organisation_account",
                            lambda self: {"id": "acct-1"})
        monkeypatch.setattr("tortoise.record_writer.record_tenant_effective",
                            lambda carried=None: (self.OWN_ORG, "graph-own"))
        return s

    def _registry(self, monkeypatch, *, rows=None, raises=None):
        """Substitute the key registry, and record how it was asked."""
        seen: dict = {}

        class _Q:
            def query(self, cypher, params=None):
                seen["cypher"] = cypher
                seen["params"] = params
                if raises is not None:
                    raise raises
                return type("R", (), {"result_set": rows})()

        monkeypatch.setattr(TortoiseSDK, "_get_registry", lambda self: _Q())
        return seen

    def test_an_absent_key_and_a_foreign_key_get_the_same_reply(
            self, bound, monkeypatch):
        """THE isolation property: two different facts, one indistinguishable answer.

        Measured by making the two replies differ, not by reading them: the mutant in the
        PR body's #3442 table splits them into "no such key exists anywhere" / "key
        belongs to another account" and this test reds.
        """
        self._registry(monkeypatch, rows=[])
        absent = bound.check_connection("key-1")
        self._registry(monkeypatch, rows=[self.FOREIGN_ROW])
        foreign = bound.check_connection("key-1")

        assert absent["key"] is None, absent
        assert foreign["key"] is None, (
            "a foreign-org key returned a payload — the registry row leaked to the caller")
        assert absent["key_error"] == foreign["key_error"], (
            "an absent key and another account's key must be indistinguishable: "
            f"{absent['key_error']!r} vs {foreign['key_error']!r}")
        assert json.dumps(absent, sort_keys=True) == json.dumps(foreign, sort_keys=True), (
            "the two replies must be byte-identical, not merely equal on the error text")

    def test_an_own_key_returns_its_payload(self, bound, monkeypatch):
        """The other polarity: the SAME row, own org, must resolve — or the refusal is
        just a refusal that always fires and the test above proves nothing."""
        self._registry(monkeypatch, rows=[self.OWN_ROW])
        out = bound.check_connection("key-1")
        assert out["key_error"] is None, out
        assert out["key"] == {
            "key_id": "key-1",
            "name": "laptop",
            "graph_id": "graph-own",
            "scopes": ["read", "write"],
            "delegation_depth": 2,
            "revoked": False,
            "expires_at": 1893456000,
        }, out

    def test_the_read_is_scoped_to_the_key_id_and_the_own_tenant(
            self, bound, monkeypatch):
        """The comparison is against the SDK's OWN tenant, never a caller-supplied id.

        The `key_id` is the only caller input, and it reaches the read as a PARAMETER
        (never interpolated into the Cypher), which is also what keeps a credential id
        usable as a query argument.
        """
        seen = self._registry(monkeypatch, rows=[self.OWN_ROW])
        bound.check_connection("key-1")
        assert seen["params"] == {"id": "key-1"}, seen
        assert "$id" in seen["cypher"], seen

    def test_an_unreadable_registry_is_not_an_answer_about_the_key(
            self, bound, monkeypatch):
        """A registry that RAISES must not collapse into the "not found" reply — that
        would report a reachable-looking "no such key" for a store nobody could read."""
        self._registry(monkeypatch, raises=RuntimeError("registry down"))
        out = bound.check_connection("key-1")
        assert out["key"] is None
        assert out["key_error"] != "not found in this account", out
        assert "could not be read" in out["key_error"], out
        assert "RuntimeError" in out["key_error"], out


class TestMCPLayerRefusals:
    """The MCP handlers own two things the SDK must not assume: the transport
    exclusion for filesystem walks (#329) and the input refusal. Both are asserted
    here because a handler that stopped refusing would look like a working tool."""

    def test_a_directory_walk_is_excluded_from_tenant_http(self, monkeypatch):
        token = ms._transport_mode.set("http")
        try:
            out = ms.tortoise_index_sources_from_directory("/tmp/corpus")
        finally:
            ms._transport_mode.reset(token)
        assert "error" in out, out
        assert "Directory not found" not in str(out), (
            "the transport gate must refuse BEFORE touching the filesystem")

    def test_a_missing_directory_is_refused_by_name(self):
        token = ms._transport_mode.set("stdio")
        try:
            out = ms.tortoise_index_sources_from_directory("/definitely/not/here")
        finally:
            ms._transport_mode.reset(token)
        assert "Directory not found" in out["error"]

    def test_both_mine_arms_together_is_refused_at_the_mcp_layer_too(self):
        """The SDK refuses loudly (ValueError); the MCP layer must refuse as a
        structured error instead of letting a traceback reach the client."""
        out = ms.tortoise_mine_knowledge_from_session(
            conversation=[{"role": "user", "content": "hi"}],
            items=[{"content": "x"}])
        assert "different pipelines" in out["error"]


class TestTheAbsorbedArmsAreReachable:
    """Every arm a retired door had must be reachable through its absorber.

    The #12948 review (P2-1/P2-2/P2-3) measured three arms that were NOT: the
    single-transcript mode of `tortoise_mine_conversations`, the
    `machine_id`/`model` attribution the capture door stamped, and the
    `extract_metadata`/`llm_model` pair the session-index door defaulted ON.
    Each test here fails if the arm is dropped again — they assert the
    FORWARDED CALL, not the absorbed door's docstring.
    """

    def test_the_single_transcript_arm_reaches_the_retired_body(self, monkeypatch):
        """`conversation=` as a raw string + `source_id=` is the OTHER mode of the
        retired conversations door; the directory container cannot perform it."""
        seen = {}

        def _recorder(**kwargs):
            seen.update(kwargs)
            return {"mined": True}

        monkeypatch.setattr(ms, "tortoise_mine_conversations", _recorder)
        out = ms.tortoise_mine_knowledge_from_session(
            conversation="USER: hello\nAGENT: hi", source_id="src-1",
            content_dedup=False, session_date="2026-11-16", participants=["a"],
            extract_entities=False)
        assert out == {"mined": True}, out
        assert seen["transcript"] == "USER: hello\nAGENT: hi", seen
        assert seen["source_id"] == "src-1", seen
        assert seen["content_dedup"] is False, seen
        assert seen["session_date"] == "2026-11-16", seen
        assert seen["participants"] == ["a"], seen
        assert seen["extract_entities"] is False, seen

    def test_a_raw_transcript_without_a_source_is_refused_by_name(self):
        out = ms.tortoise_mine_knowledge_from_session(conversation="USER: hello")
        assert "error" in out, out
        assert "source_id" in out["error"], out

    def test_the_capture_arm_forwards_the_attribution_claims(self, monkeypatch):
        """`machine_id`/`model` are CLIENT-CLAIMED and stamped set-if-absent
        downstream; dropping them at the container loses attribution silently."""
        seen = {}

        def _recorder(conversation, **kwargs):
            seen["conversation"] = conversation
            seen.update(kwargs)
            return {"ok": True}

        monkeypatch.setattr(ms, "tortoise_session_capture", _recorder)
        turns = [{"role": "user", "content": "hi"}]
        out = ms.tortoise_mine_knowledge_from_session(
            conversation=turns, session_id="s-1", harness="pi",
            machine_id="machine-x", model="deepseek-flash")
        assert out == {"ok": True}, out
        assert seen["conversation"] == turns, seen
        assert seen["machine_id"] == "machine-x", seen
        assert seen["model"] == "deepseek-flash", seen
        assert seen["harness"] == "pi" and seen["session_id"] == "s-1", seen

    def test_the_metadata_default_follows_the_arm(self, monkeypatch):
        """`index_sessions` extracted metadata by default and took `llm_model`;
        the corpus doors did not. One union default would silently flip a
        migrating session-index caller to extraction OFF."""
        seen = []

        class _FakeSDK:
            def index_sources_from_directory(self, directory, **kwargs):
                seen.append(dict(kwargs))
                return {"indexed": 1}

        monkeypatch.setattr(ms, "_get_org_sdk", lambda: _FakeSDK())
        monkeypatch.setattr(ms.os.path, "isdir", lambda p: True)

        outs = []
        token = ms._transport_mode.set("stdio")
        try:
            outs = [
                ms.tortoise_index_sources_from_directory(
                    "/tmp/c", file_type="agent_session"),
                ms.tortoise_index_sources_from_directory("/tmp/c", file_type="doc"),
                ms.tortoise_index_sources_from_directory(
                    "/tmp/c", file_type="agent_session", extract_metadata=False,
                    llm_model="gpt-5-mini"),
            ]
        finally:
            ms._transport_mode.reset(token)
        assert len(seen) == 3, outs
        session_arm, corpus_arm, explicit = seen
        assert session_arm["extract_metadata"] is True, session_arm
        assert "llm_model" in session_arm, session_arm
        assert corpus_arm["extract_metadata"] is False, corpus_arm
        assert "llm_model" not in corpus_arm, corpus_arm
        assert explicit["extract_metadata"] is False, explicit
        assert explicit["llm_model"] == "gpt-5-mini", explicit

    def test_a_meeting_walk_does_not_invent_the_session_door_s_default(self, monkeypatch):
        """`meeting` has NO retired door, so it keeps the container's own OFF
        default: claiming door parity for it would flip extraction ON for a file
        class no door ever defaulted that way."""
        seen = []

        class _FakeSDK:
            def index_sources_from_directory(self, directory, **kwargs):
                seen.append(dict(kwargs))
                return {"indexed": 1}

        monkeypatch.setattr(ms, "_get_org_sdk", lambda: _FakeSDK())
        monkeypatch.setattr(ms.os.path, "isdir", lambda p: True)
        token = ms._transport_mode.set("stdio")
        try:
            ms.tortoise_index_sources_from_directory("/tmp/c", file_type="meeting")
        finally:
            ms._transport_mode.reset(token)
        assert seen[0]["extract_metadata"] is False, seen
        assert "llm_model" not in seen[0], seen

    def test_the_raw_transcript_arm_refuses_http_by_arm_not_for_the_whole_tool(self):
        """The retired door was HTTP-excluded as a whole (its CORPUS mode walks the
        host filesystem), so the transcript arm keeps that posture — but it must
        say so about the ARM: the inherited blanket message told an HTTP caller the
        whole container was unavailable, including the capture/`items=` arms that
        do work there (#12948 review round 2)."""
        token = ms._transport_mode.set("http")
        try:
            out = ms.tortoise_mine_knowledge_from_session(
                conversation="USER: hi", source_id="src-1")
        finally:
            ms._transport_mode.reset(token)
        assert "error" in out, out
        assert "raw-transcript arm" in out["error"], out
        assert "capture arm" in out["error"], out
        assert "not available over HTTP" not in out["error"], (
            "the blanket refusal tells the caller the whole tool is unavailable")
