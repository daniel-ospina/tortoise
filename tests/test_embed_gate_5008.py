"""S5 EMBED gate — the second gate on the pack's one declared rule set (#5008).

THE DEFECT THIS PINS
--------------------
`packs/dev/manifest.yaml` → `ontology.memory_granularity` declares the ephemeral
class, and the **only** consumer rendered it into a prompt: nothing inspected a
candidate row and decided *embed / don't-embed*. `config/v4-mechanisms.yml`
carried the mechanism as `not-implemented, tracking_issue: 5008`, and the cost
model makes that absence permanent rather than cosmetic — FalkorDB Cloud bills
**provisioned RAM with no eviction**, so every stored vector is rent forever,
whether or not any query ever matches it (`STORAGE-ARCHITECTURE.md` §12.2).

What `tortoise/embed_gate.py` + `embeddings.encode_for_row` now enforce, and
what each test below is load-bearing for:

- **One rule set, two gates** (§12.2a, DECIDED). The EMBED gate must NOT derive
  its own patterns — *"a second, independently-derived rule set for EMBED would
  be exactly the drift that criterion exists to prevent."* So `RULE_SET` **is**
  `value_gate.RULES` and every id this gate can emit is a member of it.
- **The flag is the deferral, not a nag.** Off by default ⇒ the write path is
  byte-identical to pre-`#5008`; `#5008` asks for a draft-and-run on tens of
  rows before the rate effect is trusted.
- **Both directions of `#2453`.** A row where a decision *turned on* an
  identifier must keep its vector; a row that is *nothing but* an identifier
  loses it.
- **How the EMBED question differs from the RETENTION declaration.** §12.2a
  warns that `memory_granularity` is a retention policy whose ephemeral list
  contains plainly **semantic** members (*"tool workarounds"*, *"sprint
  mechanics"*). Those earn a vector; the gate states and pins that difference.
- **Fail-open is structural**, because the two mistakes are not symmetric: a
  false `NO-EMBED` destroys recall for a row nobody can re-find, while a false
  `EMBED` costs bytes and is recoverable from the text the node still carries.
- **The write path actually consults it** — the enforcement half is asserted
  end-to-end on a real store (a written row still EXISTS and carries no
  vector), not inferred from the predicate alone.

Run (embedded lane; URI-less by design)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_embed_gate_5008.py -q

"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import embed_gate, value_gate
from tortoise.embeddings import EMBEDDING_DIM, encode_for_row, encode_for_store
from tortoise.sdk import TortoiseSDK

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"
_DIM = EMBEDDING_DIM if isinstance(EMBEDDING_DIM, int) else 384
_FLAG = "TORTOISE_EMBED_GATE"

#: One text per SHARED rule class — the four the value gate can emit. Drawn from
#: the identifier shapes real extraction rows carry (`#4899`'s Stage-1 sample).
IDENTIFIER_ONLY = {
    value_gate.RULE_REFERENCE: "#4716",
    value_gate.RULE_HASH: "9bce8d31f",
    value_gate.RULE_PATH: "src/app.test.tsx",
    value_gate.RULE_UUID: "550e8400-e29b-41d4-a716-446655440000",
}


def _embed_a(text: str, max_tokens: int = 512):
    """Deterministic, content-dependent vector (component 0 = text length)."""
    return [float(len(text))] + [0.25] * (_DIM - 1)


def _on(monkeypatch):
    monkeypatch.setenv(_FLAG, "1")


# ── the flag: off by default, and inert when off ──────────────────────────


def test_the_gate_is_off_by_default(monkeypatch):
    """Unset ⇒ the gate does not run at all — the pre-#5008 graph, unchanged."""
    monkeypatch.delenv(_FLAG, raising=False)
    assert embed_gate.embed_gate_enabled() is False


def test_off_by_default_means_an_identifier_only_row_still_gets_its_vector(monkeypatch):
    """The deferral is real: with the flag unset, nothing changes on the write path."""
    monkeypatch.delenv(_FLAG, raising=False)
    with mock.patch(_EMBED_PATCH, mock.MagicMock(side_effect=_embed_a)) as enc:
        assert encode_for_row("#4716", None) == _embed_a("#4716")
    assert enc.called, "the encoder must still be consulted while the gate is off"


def test_the_flag_reads_the_declared_truthy_contract(monkeypatch):
    for value in ("1", "true", "yes", "TRUE"):
        monkeypatch.setenv(_FLAG, value)
        assert embed_gate.embed_gate_enabled() is True, value
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv(_FLAG, value)
        assert embed_gate.embed_gate_enabled() is False, value


# ── one rule set, two gates (§12.2a's DECIDED shape) ──────────────────────


def test_the_rule_set_is_the_value_gate_s_own(monkeypatch):
    """NOT a copy: §12.2a forbids a second, independently-derived rule set."""
    assert embed_gate.RULE_SET is value_gate.RULES
    # ...and the gate carries no pattern of its own to drift with.
    for pattern in ("_UUID", "_HASH", "_REFERENCE", "_PATH", "_FUNCTION_WORDS"):
        assert not hasattr(embed_gate, pattern), (
            f"{pattern} re-declared in embed_gate — the rule set must have ONE home"
        )


@pytest.mark.parametrize("rule_id,text", sorted(IDENTIFIER_ONLY.items()))
def test_every_emitted_rule_id_is_a_shared_rule_id(rule_id, text):
    """Every id this gate can emit is a member of the value gate's rule set."""
    verdict = embed_gate.embedding_verdict(text)
    assert verdict is not None
    assert verdict["rule_id"] == rule_id
    assert verdict["rule_id"] in value_gate.RULES


def test_the_verdict_asks_the_embed_question_and_quotes_the_text():
    """A NO-EMBED must not be readable as a DISCARD: the gate names itself.

    The two gates act on the same text, so the recorded evidence has to say
    which question produced the decision (`#4899` safeguard 2 — no silent, and
    no ambiguous, decision).
    """
    verdict = embed_gate.embedding_verdict("#4716")
    assert verdict is not None
    assert verdict["gate"] == "embed"
    assert "#4716" in verdict["reason"]
    assert "no vector" in verdict["reason"]


# ── both directions of #2453 ──────────────────────────────────────────────


@pytest.mark.parametrize("rule_id,text", sorted(IDENTIFIER_ONLY.items()))
def test_an_identifier_only_row_earns_no_vector(rule_id, text):
    assert embed_gate.embedding_verdict(text) is not None


#: Real rows where an identifier is *incident to* a claim — the direction that
#: costs recall if the gate over-fires. All must EMBED.
DECISION_RELEVANT = [
    "The decision turned on #4118",
    "The _support_cdata override, the fix for #4118 divergence 2, is exercised "
    "by no test: monkeypatching the property back to True leaves all 18 -k id_ "
    "tests green.",
    "docs/auth-architecture.md:93 records the cookie policy for the subdomain.",
    "Committed as 9bce8d31f, and that commit is what fixed the fold.",
    "The plan doc is the source of truth for the migration order.",
]


@pytest.mark.parametrize("text", DECISION_RELEVANT)
def test_a_decision_relevant_row_that_mentions_an_identifier_still_earns_a_vector(text):
    assert embed_gate.embedding_verdict(text) is None, (
        "an identifier PRESENT in a claim must never cost the claim its vector"
    )


# ── how the EMBED question differs from the RETENTION declaration ─────────

#: `memory_granularity`'s ephemeral list, member by member. The identifier
#: members belong to the predicate above; the SEMANTIC members ("tool
#: workarounds", "sprint mechanics") are ephemeral-but-meaningful and MUST keep
#: a vector — that is the difference §12.2a requires the gate to state.
SEMANTIC_EPHEMERAL = [
    "The tool workaround: batching the writes by 200 keeps the fold from "
    "re-entering the guard that timed out on the second pass.",
    "Sprint mechanics: the retry budget resets at the checkpoint, so a failed "
    "shard is re-driven on the next cycle rather than replayed from the start.",
    "CI status: the leg went red because the shard ran out of spawn budget.",
    "Test counts: 72 files carry 4,498 test functions in the slow set.",
]


@pytest.mark.parametrize("text", SEMANTIC_EPHEMERAL)
def test_the_semantic_members_of_the_ephemeral_class_still_earn_a_vector(text):
    assert embed_gate.embedding_verdict(text) is None, (
        "a workaround or a sprint mechanic carries meaning its identifiers do "
        "not — the retention declaration is not a meaning predicate"
    )


def test_a_bare_measurement_is_not_an_identifier(monkeypatch):
    """`#2453` requires decision-relevant operational values verbatim."""
    assert embed_gate.embedding_verdict("1234567") is None
    assert embed_gate.embedding_verdict("2026-09-19") is None
    assert embed_gate.embedding_verdict("12.5") is None


# ── fail-open, structurally ───────────────────────────────────────────────


def test_a_predicate_that_raises_keeps_the_vector(monkeypatch):
    """A gate that cannot decide must never be the reason recall disappeared."""

    def _boom(_text):
        raise RuntimeError("predicate exploded")

    monkeypatch.setattr(value_gate, "identifier_only", _boom)
    assert embed_gate.embedding_verdict("#4716") is None
    with mock.patch(_EMBED_PATCH, _embed_a):
        assert encode_for_row("#4716", None) == _embed_a("#4716")


@pytest.mark.parametrize("candidate", [None, "", "   ", 0, [], {}, True])
def test_an_unparseable_candidate_keeps_the_vector(candidate):
    assert embed_gate.embedding_verdict(candidate) is None


# ── the seam, not the predicate: what a WRITE does ────────────────────────


def test_the_raw_primitive_is_not_gated(monkeypatch):
    """Reads, backfills and repairs call `encode_for_store` and must stay ungated.

    The gate belongs to the SUBMISSION step, not to the encoder: gating the
    primitive would silently strip vectors from the backfill that is supposed to
    restore them.
    """
    _on(monkeypatch)
    with mock.patch(_EMBED_PATCH, _embed_a):
        assert encode_for_store("#4716", None) == _embed_a("#4716")
        assert encode_for_row("#4716", None) is None
        assert encode_for_row("The decision turned on #4118", None) is not None


def test_a_gated_row_is_logged_with_the_shared_rule_id(monkeypatch, caplog):
    """The measurement `#5008` asks for: a draft-and-run can be counted from the log."""
    _on(monkeypatch)
    with mock.patch(_EMBED_PATCH, _embed_a), caplog.at_level("INFO"):
        encode_for_row("9bce8d31f", None)
    records = [r.getMessage() for r in caplog.records]
    assert any("embed gate" in m and value_gate.RULE_HASH in m for m in records), records


# ── end-to-end: the writer consults the gate and the ROW still exists ─────


@pytest.fixture
def sdk(tmp_path):
    """A real store with a deterministic embedder (the #4208 pattern)."""
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "graph.db"),
                          event_log_path=str(events / "events.jsonl"))
    yield sdk
    sdk.close()


def _stored_embedding(sdk: TortoiseSDK, label: str, key: str, value: str):
    rows = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{{key}:$v}}) RETURN n.embedding", params={"v": value},
    ).result_set
    assert rows, f"{label} {value!r} is missing from the graph"
    return None if rows[0][0] is None else list(rows[0][0])


def test_a_written_identifier_only_point_still_exists_and_carries_no_vector(
    sdk, monkeypatch,
):
    """FAILS BEFORE #5008: the write path encodes it and pays the rent forever.

    The row is NOT dropped — that is the DISCARD gate's question, and it is a
    different gate. Here the node exists, is retrievable, and simply has no
    vector.
    """
    _on(monkeypatch)
    with mock.patch(_EMBED_PATCH, _embed_a):
        pid = sdk.create_point("statement", "#4716").get("id")
    assert _stored_embedding(sdk, "Point", "id", pid) is None, (
        "an identifier-only row must not carry a vector"
    )
    assert sdk.get_point(pid)["content"] == "#4716", (
        "the gate must not drop the row — no-vector is not no-row"
    )


def test_the_same_point_keeps_its_vector_when_the_flag_is_off(sdk, monkeypatch):
    """The control: same write, gate off ⇒ vector — the deferral is the diff."""
    monkeypatch.delenv(_FLAG, raising=False)
    with mock.patch(_EMBED_PATCH, _embed_a):
        pid = sdk.create_point("statement", "#4716").get("id")
    assert _stored_embedding(sdk, "Point", "id", pid) == _embed_a("#4716")


def test_a_meaningful_point_keeps_its_vector_with_the_gate_on(sdk, monkeypatch):
    _on(monkeypatch)
    with mock.patch(_EMBED_PATCH, _embed_a):
        content = "The decision turned on #4118"
        pid = sdk.create_point("statement", content).get("id")
    assert _stored_embedding(sdk, "Point", "id", pid) == _embed_a(content)


def test_an_identifier_only_entity_name_is_gated_on_the_projection_seam(
    sdk, monkeypatch,
):
    """The entity writers are the same submission step (Object/Subject/Event).

    An `:Object` named after a path is the `#4997` population in miniature: a
    vector over a bare identifier, paid for on every re-write, matched by a
    FULLTEXT index that exists for exactly this job.
    """
    _on(monkeypatch)
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk._get_proj()._upsert_object(
            {"id": "obj-pathed", "name": "src/app.test.tsx",
             "object_kind": "core:artifact"},
        )
    assert _stored_embedding(sdk, "Object", "id", "obj-pathed") is None
    assert sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) RETURN o.name", params={"id": "obj-pathed"},
    ).result_set, "the Object must still be written"
