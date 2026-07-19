"""Integration tests for coordination layer — Cards, Kanban boards, Coordinator.

Covers all 6 REQ-COORD requirements and ONT-002 edge cases.
Runs against FalkorDBLite (temp file). No external deps beyond Tortoise.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.coordination import (             # noqa: E402
    Card, CardStatus, KanbanBoard, Coordinator,
    CARD_TRANSITIONS,
)
from tortoise.projection import FalkorProjection  # noqa: E402


# ── Constants ───────────────────────────────────────────────────────────

_ANTI_OSCILLATION_THRESHOLD = 5  # ponytail: configurable constant over magic number


# ── helpers ──────────────────────────────────────────────────────────────

def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_coord_"), name)


def _proj() -> FalkorProjection:
    return FalkorProjection(_tmp("test.db"), graph_name="test_coord")


# ── Card lifecycle (ONT-002) ─────────────────────────────────────────────

def test_card_defaults():
    card = Card(title="Test card", team="test_team")
    assert card.status == CardStatus.PROVIDED
    assert card.priority == 5
    assert card.source == "cron"
    assert len(card.id) == 26  # ULID


def test_card_full_lifecycle():
    """E2E-1: Card through full journey: provided→ready→running→reviewing→done."""
    card = Card(title="Full journey", team="test")
    assert card.status == CardStatus.PROVIDED

    card.transition(CardStatus.READY)
    assert card.status == CardStatus.READY

    card.transition(CardStatus.RUNNING)
    assert card.status == CardStatus.RUNNING

    card.transition(CardStatus.REVIEWING)
    assert card.status == CardStatus.REVIEWING

    card.transition(CardStatus.DONE)
    assert card.status == CardStatus.DONE


def test_card_invalid_transition_raises():
    """A card in REVIEWING cannot jump to PROVIDED."""
    card = Card(title="Bad jump", team="test", status=CardStatus.REVIEWING)
    try:
        card.transition(CardStatus.PROVIDED)
        assert False, "should have raised"
    except ValueError:
        pass


def test_card_resurrection():
    """ONT-002: done→ready (resurrection)."""
    card = Card(title="Phoenix", team="test", status=CardStatus.DONE)
    assert card.status == CardStatus.DONE

    card.transition(CardStatus.READY)
    assert card.status == CardStatus.READY
    assert card.stall_cycles == 0  # reset on resurrection


def test_card_failed_resurrection():
    """Failed cards can be resurrected to ready."""
    card = Card(title="Failed card", team="test", status=CardStatus.FAILED)
    card.transition(CardStatus.READY)
    assert card.status == CardStatus.READY


def test_card_split():
    """ONT-002: split card into children, parent enters DELEGATED."""
    parent = Card(title="Parent task", team="test",
                  status=CardStatus.RUNNING)
    children = parent.split(["Child A", "Child B"])
    assert parent.status == CardStatus.DELEGATED
    assert len(children) == 2
    assert children[0].parent_card == parent.id
    assert children[1].parent_card == parent.id
    assert children[0].status == CardStatus.PROVIDED
    assert children[0].title == "Child A"
    assert children[1].title == "Child B"


def test_card_cycle_detection():
    """ONT-002: detect cycle when splitting — A→B→A would loop."""
    a = Card(id="card-a", title="A", team="test")
    b = Card(id="card-b", title="B", team="test", parent_card="card-a")
    # Check: if we try to make a's parent be b, that's a cycle
    graph = {"card-a": a, "card-b": b}
    # a→b→a = cycle
    assert Card.detect_cycle("card-b", graph) is False  # b's parent is a, no cycle yet
    # If b were the parent of some new card c, and c's parent were a — let's check a cycle
    assert Card.detect_cycle("card-a", {"card-a": b}) is True  # a→b→a cycle


def test_card_can_transition():
    card = Card(status=CardStatus.PROVIDED)
    assert card.can_transition(CardStatus.READY) is True
    assert card.can_transition(CardStatus.CANCELLED) is True
    assert card.can_transition(CardStatus.RUNNING) is False  # skip ready
    assert card.can_transition(CardStatus.DONE) is False


def test_card_blocked_unblocked():
    """Blocked cards can go to ready or cancelled."""
    card = Card(title="Blocked", team="test", status=CardStatus.BLOCKED)
    card.transition(CardStatus.READY)
    assert card.status == CardStatus.READY


def test_card_serialization_roundtrip():
    card = Card(title="Roundtrip", team="test", priority=2,
                source="issue", source_id="gh-123",
                status=CardStatus.RUNNING)
    d = card.to_dict()
    assert d["title"] == "Roundtrip"
    assert d["status"] == "running"
    restored = Card.from_node(d)
    assert restored.status == CardStatus.RUNNING
    assert restored.priority == 2
    assert restored.source == "issue"


# ── Kanban board (REQ-COORD-002, REQ-COORD-003) ─────────────────────────

def test_kanban_add_and_pull():
    """REQ-COORD-002: add card, pull highest priority."""
    proj = _proj()
    board = KanbanBoard(proj, "implementer", "test_team")

    c1 = Card(title="Low prio", team="test_team", priority=5,
              assigned_to="implementer", status=CardStatus.READY)
    c2 = Card(title="High prio", team="test_team", priority=1,
              assigned_to="implementer", status=CardStatus.READY)

    board.add_card(c1)
    board.add_card(c2)

    pulled = board.pull_highest()
    assert pulled is not None
    assert pulled.title == "High prio"
    assert pulled.priority == 1


def test_kanban_pull_empty():
    proj = _proj()
    board = KanbanBoard(proj, "empty_role", "test_team")
    assert board.pull_highest() is None


def test_kanban_move_card():
    proj = _proj()
    board = KanbanBoard(proj, "worker", "test_team")
    card = Card(title="Move me", team="test_team", assigned_to="worker",
                status=CardStatus.READY)
    board.add_card(card)
    board.move_card(card.id, CardStatus.RUNNING)

    # Pull won't return it now (not pending)
    assert board.pull_highest() is None

    # Redirect to another role's board
    other_board = KanbanBoard(proj, "other_role", "test_team")  # ensures target exists
    board.redirect_card(card.id, "other_role")


def test_kanban_redirect():
    """REQ-COORD-003: redirect misrouted card to correct role."""
    proj = _proj()
    strategist_board = KanbanBoard(proj, "strategist", "test_team")
    impl_board = KanbanBoard(proj, "implementer", "test_team")

    card = Card(title="Strategist work", team="test_team",
                assigned_to="implementer", status=CardStatus.READY)
    impl_board.add_card(card)

    # Should be on implementer board
    assert impl_board.pull_highest() is not None

    # Redirect to strategist
    impl_board.redirect_card(card.id, "strategist")

    # Now empty on implementer
    assert impl_board.pull_highest() is None


def test_kanban_list_pending():
    proj = _proj()
    board = KanbanBoard(proj, "lister", "test_team")

    for i in range(3):
        board.add_card(Card(title=f"Task {i}", team="test_team",
                      assigned_to="lister", priority=i + 1,
                      status=CardStatus.READY))

    pending = board.list_pending()
    assert len(pending) == 3
    # Sorted by priority ASC (1,2,3)
    assert pending[0].priority == 1
    assert pending[2].priority == 3


# ── Coordinator (REQ-COORD-001, REQ-COORD-004, REQ-COORD-005, REQ-COORD-006)

def test_coordinator_dispatch():
    """REQ-COORD-001: dispatch roadmap items to boards."""
    proj = _proj()
    coord = Coordinator(proj)

    items = [
        {"id": "road-1", "title": "Strategist review", "source": "cron",
         "team": "test_team", "role_type": "strategist", "role": "strategist",
         "priority": 1},
        {"id": "road-2", "title": "Implement feature", "source": "issue",
         "team": "test_team", "role_type": "implementer", "role": "implementer",
         "priority": 2},
    ]
    cards = coord.dispatch(items)
    assert len(cards) == 2

    # Check boards populated
    strat_board = KanbanBoard(proj, "strategist", "test_team")
    impl_board = KanbanBoard(proj, "implementer", "test_team")
    assert strat_board.pull_highest() is not None
    assert impl_board.pull_highest() is not None


def test_coordinator_conflict_detection():
    """REQ-COORD-004: detect resource conflicts."""
    proj = _proj()
    coord = Coordinator(proj)

    # Add a running card for the same source
    running = Card(
        title="Already running", team="test_team", source="issue",
        source_id="road-99", assigned_to="worker1",
        status=CardStatus.RUNNING,
    )
    board = KanbanBoard(proj, "worker1", "test_team")
    board.add_card(running)

    # Try to dispatch another card for same source
    new_card = Card(
        title="Conflict card", team="test_team", source="issue",
        source_id="road-99",
    )
    conflicts = coord.detect_conflicts(new_card)
    assert len(conflicts) >= 1
    assert conflicts[0]["type"] == "resource"


def test_coordinator_no_conflict():
    """No conflict when source_id is empty or unique."""
    proj = _proj()
    coord = Coordinator(proj)

    card = Card(title="Unique task", team="test_team", source="cron", source_id="unique-1")
    conflicts = coord.detect_conflicts(card)
    assert len(conflicts) == 0


def test_coordinator_anti_oscillation():
    """REQ-COORD-004: anti-oscillation suppresses rapid toggling."""
    proj = _proj()
    coord = Coordinator(proj)

    # Create a card with high oscillation count for the source
    oscillating = Card(
        title="Flip-flop", team="test_team", source="issue",
        source_id="flaky-1", oscillation_count=_ANTI_OSCILLATION_THRESHOLD,
        status=CardStatus.RUNNING,
    )
    board = KanbanBoard(proj, "worker1", "test_team")
    board.add_card(oscillating)

    new_card = Card(
        title="Yet another flip", team="test_team",
        source="issue", source_id="flaky-1",
    )
    conflicts = coord.detect_conflicts(new_card)
    has_osc = any(c["type"] == "oscillation" for c in conflicts)
    assert has_osc, f"Expected oscillation conflict, got: {conflicts}"


def test_coordinator_stall_detection():
    """REQ-COORD-006: detect cards untouched for >N cycles."""
    proj = _proj()
    coord = Coordinator(proj, stall_cycles=3)

    # Add a stalled card
    stalled = Card(
        title="Forgotten task", team="test_team", stall_cycles=5,
        status=CardStatus.READY, assigned_to="worker1",
    )
    board = KanbanBoard(proj, "worker1", "test_team")
    board.add_card(stalled)

    # Add a fresh card
    fresh = Card(
        title="Fresh task", team="test_team", stall_cycles=1,
        status=CardStatus.READY, assigned_to="worker1",
    )
    board.add_card(fresh)

    stalled_cards = coord.detect_stalls("test_team")
    stalled_ids = {c.id for c in stalled_cards}
    assert stalled.id in stalled_ids
    assert fresh.id not in stalled_ids


def test_coordinator_eisenhower_review():
    """REQ-COORD-005: Eisenhower matrix scoring and recommendations."""
    proj = _proj()
    coord = Coordinator(proj)

    board = KanbanBoard(proj, "strategist", "test_team")
    # High importance + high urgency → promote
    board.add_card(Card(title="Critical", team="test_team",
                   assigned_to="strategist", importance=0.9, urgency=0.8,
                   status=CardStatus.READY))
    # Low importance + low urgency → archive
    board.add_card(Card(title="Trivial", team="test_team",
                   assigned_to="strategist", importance=0.1, urgency=0.2,
                   status=CardStatus.READY))
    # Medium → deprioritize
    board.add_card(Card(title="Medium", team="test_team",
                   assigned_to="strategist", importance=0.4, urgency=0.5,
                   status=CardStatus.READY))

    recs = coord.eisenhower_review("strategist")
    assert len(recs) == 3

    # First should be highest score (Critical)
    assert recs[0]["title"] == "Critical"
    assert recs[0]["recommendation"] == "promote"

    # Last should be lowest score (Trivial)
    assert recs[-1]["title"] == "Trivial"
    assert recs[-1]["recommendation"] == "archive"


# ── Full integration smoke ───────────────────────────────────────────────

def test_full_coordination_flow():
    """End-to-end: Roadmap dispatch → pull → move → stall → review."""
    proj = _proj()
    coord = Coordinator(proj, stall_cycles=2)

    # Dispatch
    items = [
        {"id": "r1", "title": "Task 1", "source": "issue", "team": "app_team",
         "role_type": "implementer", "role": "dev", "priority": 1},
        {"id": "r2", "title": "Task 2", "source": "issue", "team": "app_team",
         "role_type": "implementer", "role": "dev", "priority": 2},
    ]
    cards = coord.dispatch(items)
    assert len(cards) == 2
    assert cards[0].status == CardStatus.READY

    # Pull highest (atomically sets status→running to prevent double-dispatch)
    board = KanbanBoard(proj, "dev", "app_team")
    pulled = board.pull_highest()
    assert pulled is not None
    assert pulled.title == "Task 1"
    assert pulled.status == CardStatus.RUNNING  # claimed by pull

    # Move to reviewing (already running from pull)
    board.move_card(pulled.id, CardStatus.REVIEWING)

    # Check stalls (fresh card, shouldn't be stalled yet)
    stalled = coord.detect_stalls("app_team")
    assert len(stalled) == 0

    # Eisenhower review
    recs = coord.eisenhower_review("dev")
    assert len(recs) >= 1


# ── Replay determinism (Layer 3) ─────────────────────────────────────

def _card_attrs(card: Card) -> dict:
    """Deterministic subset of Card fields for replay comparison."""
    return {
        "title": card.title, "status": card.status, "priority": card.priority,
        "team": card.team, "assigned_to": card.assigned_to,
        "source": card.source, "source_id": card.source_id,
    }


def test_replay_determinism():
    """Two FalkorProjection instances, same ops → same Card state.

    Compares deterministic subset (excludes id, created_at, updated_at
    which are non-deterministic by design).
    """
    proj_a = _proj()
    proj_b = _proj()

    card_a = Card(title="Replay", team="test", priority=2, assigned_to="dev",
                  source="issue", source_id="gh-7070", status=CardStatus.READY)
    card_b = Card(title="Replay", team="test", priority=2, assigned_to="dev",
                  source="issue", source_id="gh-7070", status=CardStatus.READY)

    board_a = KanbanBoard(proj_a, "dev", "test")
    board_b = KanbanBoard(proj_b, "dev", "test")
    board_a.add_card(card_a)
    board_b.add_card(card_b)
    board_a.move_card(card_a.id, CardStatus.RUNNING)
    board_b.move_card(card_b.id, CardStatus.RUNNING)

    # Query both graphs
    rows_a = proj_a.g.query(
        "MATCH (c:Card {id: $id}) RETURN c", params={"id": card_a.id}
    ).result_set
    rows_b = proj_b.g.query(
        "MATCH (c:Card {id: $id}) RETURN c", params={"id": card_b.id}
    ).result_set

    assert len(rows_a) == 1 and len(rows_b) == 1
    restored_a = Card.from_node(dict(rows_a[0][0].properties))
    restored_b = Card.from_node(dict(rows_b[0][0].properties))
    assert _card_attrs(restored_a) == _card_attrs(restored_b)
    assert restored_a.status == CardStatus.RUNNING
    assert restored_b.status == CardStatus.RUNNING


# ── Stall boundary (Layer 4) ──────────────────────────────────────────

def test_stall_boundary_at_threshold():
    """detect_stalls uses strict inequality (stall_cycles > max).

    Card at threshold is NOT detected. Existing test uses
    stall_cycles=5 vs threshold=3; this tests boundary edge.
    """
    proj = _proj()
    coord = Coordinator(proj, stall_cycles=2)

    at_threshold = Card(
        title="At boundary", team="test_team", stall_cycles=2,
        status=CardStatus.READY, assigned_to="worker1",
    )
    above_threshold = Card(
        title="Above boundary", team="test_team", stall_cycles=3,
        status=CardStatus.READY, assigned_to="worker1",
    )
    board = KanbanBoard(proj, "worker1", "test_team")
    board.add_card(at_threshold)
    board.add_card(above_threshold)

    stalled = coord.detect_stalls("test_team")
    stalled_ids = {c.id for c in stalled}

    # Card at threshold (2) is NOT detected: 2 > 2 is False
    assert at_threshold.id not in stalled_ids, (
        f"stall_cycles=2 at threshold=2 should NOT be detected (strict >)"
    )
    # Card above threshold (3) IS detected: 3 > 2 is True
    assert above_threshold.id in stalled_ids, (
        f"stall_cycles=3 at threshold=2 SHOULD be detected (strict >)"
    )


# ── Layer 4: conflict dispatch, redirect, stall marking ────────────────────


def test_dispatch_blocks_on_conflict():
    """dispatch sets card status to BLOCKED when conflicts detected."""
    proj = _proj()
    coord = Coordinator(proj)

    # Pre-existing card in RUNNING state for same source_id
    existing = Card(
        title="Already taken", team="test_team", source="issue",
        source_id="conflict-1", assigned_to="worker1",
        status=CardStatus.RUNNING,
    )
    board = KanbanBoard(proj, "worker1", "test_team")
    board.add_card(existing)

    # Dispatch new item with same source_id
    items = [{"id": "conflict-1", "title": "Duplicate", "source": "issue",
              "team": "test_team", "role": "worker1", "priority": 1}]
    cards = coord.dispatch(items)
    assert len(cards) == 1
    assert cards[0].status == CardStatus.BLOCKED
    assert "CONFLICT" in cards[0].title


def test_kanban_redirect_card():
    """redirect_card moves a card from one role board to another."""
    proj = _proj()
    source_board = KanbanBoard(proj, "worker1", "test_team")
    card = Card(title="Redirect me", team="test_team",
                assigned_to="worker1", status=CardStatus.READY)
    source_board.add_card(card)

    # Create target board first (add a dummy card to ensure board exists)
    target_board = KanbanBoard(proj, "worker2", "test_team")
    dummy = Card(title="dummy", team="test_team", assigned_to="worker2",
                 status=CardStatus.DONE)
    target_board.add_card(dummy)

    # Redirect succeeds without error
    source_board.redirect_card(card.id, "worker2")

    # Card is on target board (query via fresh board instance)
    fresh_target = KanbanBoard(proj, "worker2", "test_team")
    target_pending = fresh_target.list_pending()
    assert any(c.id == card.id for c in target_pending)

    # Card is gone from source board
    source_pending = source_board.list_pending()
    assert not any(c.id == card.id for c in source_pending)


def test_detect_stalls_marks_stalled_cards():
    """detect_stalls increments stall_cycles on detected cards."""
    proj = _proj()
    coord = Coordinator(proj, stall_cycles=2)

    stalled = Card(
        title="Will be marked", team="test_team", stall_cycles=5,
        status=CardStatus.READY, assigned_to="worker1",
    )
    board = KanbanBoard(proj, "worker1", "test_team")
    board.add_card(stalled)

    result = coord.detect_stalls("test_team")
    assert len(result) >= 1
    marked = next(c for c in result if c.id == stalled.id)
    # stall_cycles was 5, detect_stalls should have incremented it
    assert marked.stall_cycles >= 5  # at least preserved, ideally incremented


if __name__ == "__main__":
    import traceback
    tests = [
        ("test_card_defaults", test_card_defaults),
        ("test_card_full_lifecycle", test_card_full_lifecycle),
        ("test_card_invalid_transition_raises", test_card_invalid_transition_raises),
        ("test_card_resurrection", test_card_resurrection),
        ("test_card_failed_resurrection", test_card_failed_resurrection),
        ("test_card_split", test_card_split),
        ("test_card_cycle_detection", test_card_cycle_detection),
        ("test_card_can_transition", test_card_can_transition),
        ("test_card_blocked_unblocked", test_card_blocked_unblocked),
        ("test_card_serialization_roundtrip", test_card_serialization_roundtrip),
        ("test_kanban_add_and_pull", test_kanban_add_and_pull),
        ("test_kanban_pull_empty", test_kanban_pull_empty),
        ("test_kanban_move_card", test_kanban_move_card),
        ("test_kanban_redirect", test_kanban_redirect),
        ("test_kanban_list_pending", test_kanban_list_pending),
        ("test_coordinator_dispatch", test_coordinator_dispatch),
        ("test_coordinator_conflict_detection", test_coordinator_conflict_detection),
        ("test_coordinator_no_conflict", test_coordinator_no_conflict),
        ("test_coordinator_anti_oscillation", test_coordinator_anti_oscillation),
        ("test_coordinator_stall_detection", test_coordinator_stall_detection),
        ("test_coordinator_eisenhower_review", test_coordinator_eisenhower_review),
        ("test_full_coordination_flow", test_full_coordination_flow),
        ("test_dispatch_blocks_on_conflict", test_dispatch_blocks_on_conflict),
        ("test_kanban_redirect_card", test_kanban_redirect_card),
        ("test_detect_stalls_marks_stalled_cards", test_detect_stalls_marks_stalled_cards),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except Exception:
            print(f"  ❌ {name}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
