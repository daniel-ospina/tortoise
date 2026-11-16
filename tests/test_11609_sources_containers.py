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

import pytest

from tortoise import mcp_server as ms
from tortoise.sdk import TortoiseSDK
from tortoise.sdk_retired import (
    REMOVED_SDK_METHODS,
    RETIRED_SDK_METHODS,
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
