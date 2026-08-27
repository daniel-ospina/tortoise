"""Tests for the deterministic pack-chain enforcer (issue #1695, Task 1).

The chain enforcer makes the S2/S4 prompts' advisory "WARN, then TRY TO
REPAIR" semantics GUARANTEED: ``validate_and_rewire`` rewires reverse-chain-
order ``about_entities`` co-mention pairs via the nearest valid chain
intermediate — ONLY when that position is unambiguous — and warns-and-keeps
(never invents, never drops) otherwise. ``validate_chains`` (extractor_v2)
stays as the warn-only backstop and must report NO violations on the rewired
list for fixed pairs.

Lane-agnostic: pure logic (no embeddings/LLM/DB) — runs in the embedded lane
and tier-2 docker legs alike.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_extractor_v2 import MockModel  # noqa: E402, RUF100
from tortoise import extractor_v2 as v2  # noqa: E402, RUF100
from tortoise.chain_enforcer import validate_and_rewire


def _embed_with_pair(about, kinds, extra_entities=None):
    """The migrated TestChains fixture: two entities of the given kinds +
    an item co-mentioning them; optional extra entities (intermediates)."""
    embed = {
        "entities": [
            {
                "name": "arch",
                "kind": kinds[0],
                "lifecycle": "created",
                "supersedes": None,
                "note": None,
            },
            {
                "name": "useCase",
                "kind": kinds[1],
                "lifecycle": "created",
                "supersedes": None,
                "note": None,
            },
        ],
        "events": [],
        "operators": [],
        "chain_notes": [],
        "link_before_create": [],
        "points": [
            {
                "content": "arch connects to useCase",
                "pointKind": "statement",
                "about_entities": list(about),
            }
        ],
    }
    for ent in extra_entities or []:
        embed["entities"].append(dict(ent))
    return embed


def _about(result) -> list[str]:
    """The (single) point's about_entities in the rewired result."""
    return result["points"][0]["about_entities"]


class TestMigratedChains:
    """The TestChains golden scenarios (migrated from tests/test_extractor_v2.py)
    — now asserted against BOTH the enforcer (mutating) and the advisory
    validator (which must go clean on rewired pairs)."""

    def test_reverse_chain_rewired_when_intermediate_exists(self):
        # architecture (pos 6) connects to useCase (pos 1) — reverse chain
        # order in productDelivery; a feature (pos 2) entity is the nearest
        # valid intermediate → the enforcer REWIRES the connection through it.
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        result, notes, stats = validate_and_rewire(embed)
        assert notes, "reverse architecture→useCase must be flagged"
        assert notes[0]["action"] == "rewired"
        assert "feature" in notes[0]["note"]
        assert stats["rewired"] == 1
        # the connection path now flows useCase → feature → arch (ascending)
        assert _about(result) == ["useCase", "feature", "arch"]
        # the advisory validator must find NO violations on the rewired list
        assert v2.validate_chains(result) == []

    def test_reverse_chain_warned_without_intermediate(self):
        embed = _embed_with_pair(
            ["arch", "useCase"], ["product-strategy:architecture", "product-strategy:useCase"]
        )
        result, notes, stats = validate_and_rewire(embed)
        assert notes[0]["action"] == "warned"
        assert "do NOT invent" in notes[0]["note"]
        assert stats["warned"] == 1 and stats["rewired"] == 0
        # warn-and-keep: the list is untouched
        assert _about(result) == ["arch", "useCase"]

    def test_chain_order_ok_not_flagged(self):
        embed = _embed_with_pair(
            ["useCase", "feature"], ["product-strategy:useCase", "product-strategy:feature"]
        )
        result, notes, _stats = validate_and_rewire(embed)
        assert notes == []
        assert _about(result) == ["useCase", "feature"]
        assert v2.validate_chains(result) == []

    def test_never_blocks(self):
        # chain violations surface as notes; the enforcer never raises
        embed = _embed_with_pair(
            ["arch", "useCase"], ["product-strategy:architecture", "product-strategy:useCase"]
        )
        result, notes, _stats = validate_and_rewire(embed)
        assert any(n["action"] in ("rewired", "warned") for n in notes)
        # the pipeline's S5 still emits the point regardless
        out = v2.execute_embed(result, {}, session_id="s1")
        assert out["payload"]["points"]


class TestEnforcerFixtures:
    """The Task-1 golden fixtures: no-edge-drop, no-entity-invention,
    distance-threshold unreachable, ambiguous nearest position,
    no-pack-stratum no-op, decoupled-gate, deepcopy safety."""

    def test_no_edge_drop(self):
        """Rewiring never REMOVES a referenced entity: the rewired about list
        is a superset of the original refs (the connection survives, routed
        through the intermediate)."""
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        before = set(embed["points"][0]["about_entities"])
        result, _, _ = validate_and_rewire(embed)
        after = set(result["points"][0]["about_entities"])
        assert before.issubset(after), "no referenced entity may be dropped"
        assert len(after) == len(before) + 1  # exactly the intermediate added

    def test_no_entity_invention(self):
        """The enforcer only wires through EXISTING entities — it never
        invents one; the inserted ref must be a list entity."""
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        result, _notes, _ = validate_and_rewire(embed)
        entity_names = {e["name"] for e in result["entities"]}
        for ref in _about(result):
            assert ref in entity_names, f"{ref!r} must reference an emitted entity"
        assert "feature" in entity_names

    def test_distance_threshold_unreachable(self):
        """Distance rule: rewire ONLY via the nearest valid chain position.
        An intermediate at a FARTHER position (workflow@4) cannot repair the
        useCase@1→arch@6 pair — the nearest position above useCase (feature@2)
        is empty → unreachable → warn-and-keep (never skip to a farther hop)."""
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "workflow",
                    "kind": "product-strategy:workflow",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        result, notes, stats = validate_and_rewire(embed)
        assert notes[0]["action"] == "warned"
        assert "nearest valid chain position" in notes[0]["note"]
        assert stats["warned"] == 1 and stats["rewired"] == 0
        assert _about(result) == ["arch", "useCase"]  # untouched

    def test_ambiguous_nearest_position_never_guesses(self):
        """Two entities at the SAME nearest valid position → the rewire is
        not unique → warn-and-keep (never guess which one)."""
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
                {
                    "name": "feature v2",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
            ],
        )
        result, notes, stats = validate_and_rewire(embed)
        assert notes[0]["action"] == "warned"
        assert "ambiguous" in notes[0]["note"].lower()
        assert stats["warned"] == 1 and stats["rewired"] == 0
        assert _about(result) == ["arch", "useCase"]

    def test_pure_reorder_uses_existing_intermediate(self):
        """The intermediate is already co-mentioned but out of order:
        [arch, feature, useCase] → [useCase, feature, arch] — a rewire via
        the existing intermediate, nothing invented, nothing dropped."""
        embed = _embed_with_pair(
            ["arch", "useCase", "feature"],
            [
                "product-strategy:architecture",
                "product-strategy:useCase",
                "product-strategy:feature",
            ],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        result, notes, stats = validate_and_rewire(embed)
        assert notes and notes[0]["action"] == "rewired"
        assert _about(result) == ["useCase", "feature", "arch"]
        assert v2.validate_chains(result) == []
        assert stats["rewired"] == 1

    def test_no_pack_stratum_noop(self):
        """No chain-stratum kinds → byte-identical list, no notes."""
        embed = {
            "entities": [
                {
                    "name": "single-flash pipeline",
                    "kind": "core:plan",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
            "events": [],
            "operators": [],
            "chain_notes": [],
            "link_before_create": [],
            "points": [
                {
                    "content": "the working path",
                    "pointKind": "statement",
                    "about_entities": ["single-flash pipeline"],
                }
            ],
        }
        result, notes, stats = validate_and_rewire(embed)
        assert notes == []
        assert stats == {"items_checked": 1, "violations": 0, "rewired": 0, "warned": 0}
        assert result == embed

    def test_decoupled_gate_compact_off(self):
        """The enforcer is independent of the prompt-render mode (compact is
        OFF by default; the enforcer behaves identically either way)."""
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        result, _notes, _ = validate_and_rewire(embed)
        assert _notes[0]["action"] == "rewired"
        assert v2.validate_chains(result) == []

    def test_input_never_mutated(self):
        """validate_and_rewire returns a deep copy — the caller's list is
        untouched (byte-identity safety for the flag-off path)."""
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        snapshot = copy.deepcopy(embed)
        validate_and_rewire(embed)
        assert embed == snapshot

    def test_weird_input_never_raises(self):
        """Never-blocks: non-dict items, missing keys, None sections."""
        for embed in (
            {},
            {"entities": None, "points": None},
            {"entities": [None], "points": [{"about_entities": "nope"}]},
            {"entities": [{"name": "x"}], "points": [{"about_entities": ["x"]}]},
            {"entities": [], "points": [{"content": "c", "about_entities": ["ghost"]}]},
            "junk",  # non-dict top-level — never raises (module contract)
            None,
            42,
        ):
            result, notes, stats = validate_and_rewire(embed)  # never raises
            assert isinstance(result, dict)
            assert isinstance(notes, list)
            assert set(stats) == {"items_checked", "violations", "rewired", "warned"}

    def test_events_branch_rewired(self):
        """The same chain-order repair applies to EVENT about_entities."""
        embed = _embed_with_pair(
            ["arch", "useCase"],
            ["product-strategy:architecture", "product-strategy:useCase"],
            extra_entities=[
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                }
            ],
        )
        embed["points"] = []
        embed["events"] = [
            {
                "content": "arch connects to useCase",
                "eventKind": "core:occurrence",
                "about_entities": ["arch", "useCase"],
            }
        ]
        result, notes, _ = validate_and_rewire(embed)
        assert notes[0]["action"] == "rewired"
        assert result["events"][0]["about_entities"] == ["useCase", "feature", "arch"]
        assert v2.validate_chains(result) == []

    def test_adjacent_pair_no_position_between(self):
        """Reverse pair with NO chain position strictly between (feature@2
        first, useCase@1 second): no intermediate can exist → warn-and-keep."""
        embed = {
            "entities": [
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
                {
                    "name": "useCase",
                    "kind": "product-strategy:useCase",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
            ],
            "events": [],
            "operators": [],
            "chain_notes": [],
            "link_before_create": [],
            "points": [
                {
                    "content": "feature connects to useCase",
                    "pointKind": "statement",
                    "about_entities": ["feature", "useCase"],
                }
            ],
        }
        result, notes, _stats = validate_and_rewire(embed)
        assert notes[0]["action"] == "warned"
        assert "no intermediate chain position" in notes[0]["note"]
        assert _about(result) == ["feature", "useCase"]  # kept

    def test_distance_rule_refuses_far_hop_even_with_members(self):
        """F1 pin: [arch, workflow, useCase] with workflow@4 and NO entity at
        the nearest valid position (feature@2) → the reorder would skip hops
        2-3, violating the distance rule → warn-and-keep (documented: the
        repair routes through the NEAREST valid position only, never a far
        hop)."""
        embed = _embed_with_pair(
            ["arch", "workflow", "useCase"],
            [
                "product-strategy:architecture",
                "product-strategy:workflow",
                "product-strategy:useCase",
            ],
        )
        result, notes, stats = validate_and_rewire(embed)
        assert notes[0]["action"] == "warned"
        assert "EMPTY" in notes[0]["note"]
        assert _about(result) == ["arch", "workflow", "useCase"]  # kept
        assert stats["warned"] == 1 and stats["rewired"] == 0

    def test_two_rewirable_chains_compose(self):
        """P1 pin (final-review): TWO violating chains in ONE item with both
        intermediates present — the fixes COMPOSE (the second chain's splice
        builds on the first's repair; neither note lies about a rewire)."""
        embed = {"entities": [
            {"name": "customer", "kind": "product-strategy:customer",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "architecture", "kind": "product-strategy:architecture",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "useCase", "kind": "product-strategy:useCase",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "code", "kind": "dev:code",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "epic", "kind": "dev:epic",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "feature", "kind": "product-strategy:feature",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "issue", "kind": "dev:issue",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "cross-chain connections",
                         "pointKind": "statement",
                         "about_entities": ["customer", "architecture",
                                             "useCase", "code", "epic"]}]}
        result, notes, stats = validate_and_rewire(embed)
        rewired = [n for n in notes if n["action"] == "rewired"]
        assert len(rewired) == 2, "both chains must be rewired"
        assert stats["rewired"] == 2
        assert v2.validate_chains(result) == []

    def test_non_string_refs_carried_through_splice(self):
        """Never-drop extends to schema-violating non-string refs: the
        rewire carries them through at their slots (final-review P3 pin)."""
        embed = {"entities": [
            {"name": "arch", "kind": "product-strategy:architecture",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "useCase", "kind": "product-strategy:useCase",
             "lifecycle": "created", "supersedes": None, "note": None},
            {"name": "feature", "kind": "product-strategy:feature",
             "lifecycle": "created", "supersedes": None, "note": None}],
            "events": [], "operators": [], "chain_notes": [],
            "link_before_create": [],
            "points": [{"content": "c", "pointKind": "statement",
                         "about_entities": ["arch", 123, "useCase"]}]}
        result, _notes, _ = validate_and_rewire(embed)
        about = result["points"][0]["about_entities"]
        assert 123 in about, "non-string ref must survive the splice"
        assert "arch" in about and "useCase" in about

    def test_orchestrator_notes_authoritative_not_duplicated(self, monkeypatch):
        """F2/F3 pin at the orchestrator: when the enforcer rules a pair
        (warn-and-keep), the backstop's advisory note is NOT duplicated into
        result['chain_notes'] — the enforcer's note + the model's own
        chain_notes are authoritative."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)

        s2_body = {
            "entities": [
                {
                    "name": "arch",
                    "kind": "product-strategy:architecture",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
                {
                    "name": "useCase",
                    "kind": "product-strategy:useCase",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
            ],
            "events": [],
            "operators": [],
            "chain_notes": [
                {
                    "chain": "productDelivery",
                    "finding": "m",
                    "action": "repaired",
                    "note": "model note",
                }
            ],
            "link_before_create": [],
            "points": [
                {
                    "content": "arch connects to useCase",
                    "pointKind": "statement",
                    "about_entities": ["arch", "useCase"],
                }
            ],
        }

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "We believed X."
            if "GRAPH MAPPER" in system:
                return json.dumps(s2_body)
            if "GAP REVIEWER" in system:
                return json.dumps(
                    {
                        "entities": [],
                        "events": [],
                        "points": [],
                        "operators": [],
                        "chain_notes": [],
                        "link_before_create": [],
                    }
                )
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        conv = [{"role": "user", "content": "we decided X"}]
        out = v2.extract_session_v2(MockModel(resp), conv)
        chain_notes = out["chain_notes"]
        # enforcer note (warned) + model note present, backstop NOT duplicated:
        assert any(n["action"] == "warned" for n in chain_notes)
        assert any(n["action"] == "repaired" for n in chain_notes)  # model's own
        assert len(chain_notes) == 2  # enforcer warn + model note, no backstop dup
        assert out["chain_enforcer"]["stats"]["warned"] == 1

    def test_orchestrator_rewire_no_backstop_notes(self, monkeypatch):
        """A rewired pair produces ONLY the enforcer's note — the backstop
        goes clean on the fixed list and the model note still surfaces."""
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)

        s2_body = {
            "entities": [
                {
                    "name": "arch",
                    "kind": "product-strategy:architecture",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
                {
                    "name": "useCase",
                    "kind": "product-strategy:useCase",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
                {
                    "name": "feature",
                    "kind": "product-strategy:feature",
                    "lifecycle": "created",
                    "supersedes": None,
                    "note": None,
                },
            ],
            "events": [],
            "operators": [],
            "chain_notes": [],
            "link_before_create": [],
            "points": [
                {
                    "content": "arch connects to useCase",
                    "pointKind": "statement",
                    "about_entities": ["arch", "useCase"],
                }
            ],
        }

        def resp(system, user):
            if "STORY SUMMARIZER" in system:
                return "We believed X."
            if "GRAPH MAPPER" in system:
                return json.dumps(s2_body)
            if "GAP REVIEWER" in system:
                return json.dumps(
                    {
                        "entities": [],
                        "events": [],
                        "points": [],
                        "operators": [],
                        "chain_notes": [],
                        "link_before_create": [],
                    }
                )
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        conv = [{"role": "user", "content": "we decided X"}]
        out = v2.extract_session_v2(MockModel(resp), conv)
        assert out["chain_enforcer"]["stats"]["rewired"] == 1
        rewired = [n for n in out["chain_notes"] if n["action"] == "rewired"]
        assert len(rewired) == 1
        # the executed embed list is the REWIRED list: chain order valid
        executed_about = out["embed_list"]["points"][0]["about_entities"]
        assert executed_about == ["useCase", "feature", "arch"]
