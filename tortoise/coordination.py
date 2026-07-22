"""Coordination layer — S2 of the VSM three-layer platform .

Covers card lifecycle with edge cases. Cards stored as :Object nodes.
Stores cards and boards as :Object nodes in FalkorDB (PM domain extension).

~150 LOC, 0 external dependencies. Uses existing FalkorProjection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from .ids import now_iso, ulid

if TYPE_CHECKING:
    from .projection import FalkorProjection


# ── Card lifecycle (PM domain extension) ─────────────────────────────────
# Cards are :Object nodes with objectKind: pm:card or pm:kanbanBoard.
# CardStatus enum maps to canonical actionStatus field.

class CardStatus(Enum):
    PROVIDED = "provided"
    READY = "ready"
    RUNNING = "running"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    DELEGATED = "delegated"


# Valid transitions: source → {targets}
CARD_TRANSITIONS: dict[CardStatus, set[CardStatus]] = {
    CardStatus.PROVIDED:  {CardStatus.READY, CardStatus.CANCELLED, CardStatus.EXPIRED,
                           CardStatus.BLOCKED},
    CardStatus.READY:     {CardStatus.RUNNING, CardStatus.CANCELLED, CardStatus.EXPIRED},
    CardStatus.RUNNING:   {CardStatus.REVIEWING, CardStatus.FAILED, CardStatus.BLOCKED,
                           CardStatus.DELEGATED},
    CardStatus.REVIEWING: {CardStatus.DONE, CardStatus.FAILED, CardStatus.BLOCKED},
    CardStatus.DONE:      {CardStatus.READY},          # resurrection
    CardStatus.FAILED:    {CardStatus.READY},          # resurrection
    CardStatus.BLOCKED:   {CardStatus.READY, CardStatus.CANCELLED},
    CardStatus.CANCELLED: {CardStatus.READY},          # resurrection
    CardStatus.EXPIRED:   set(),
    CardStatus.DELEGATED: set(),                       # terminal for delegated cards
}

# Statuses that count as "pending" on Kanban board
_PENDING = {CardStatus.PROVIDED, CardStatus.READY}
_IN_PROGRESS = {CardStatus.RUNNING, CardStatus.REVIEWING}
_DONE = {CardStatus.DONE, CardStatus.FAILED, CardStatus.BLOCKED,
         CardStatus.CANCELLED, CardStatus.EXPIRED, CardStatus.DELEGATED}
_RESURRECTABLE = {CardStatus.DONE, CardStatus.FAILED, CardStatus.CANCELLED}


@dataclass
class Card:
    """Work unit on an agent's Kanban board.

    Stored as :Object with objectKind: pm:card in FalkorDB.
    CardStatus maps to the canonical actionStatus field.
    """
    id: str = field(default_factory=ulid)
    source: str = "cron"         # issue | handoff | cron | algedonic
    source_id: str = ""
    title: str = ""
    priority: int = 5            # 1=highest (Eisenhower urgent+important)
    assigned_to: str = ""
    assigned_by: str = ""
    team: str = ""
    status: CardStatus = CardStatus.PROVIDED
    importance: float = 0.5      # epistemic weight (for Eisenhower)
    urgency: float = 0.5         # recency score (for Eisenhower)
    stall_cycles: int = 0
    oscillation_count: int = 0
    parent_card: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def can_transition(self, target: CardStatus) -> bool:
        return target in CARD_TRANSITIONS.get(self.status, set())

    def transition(self, target: CardStatus) -> Card:
        if not self.can_transition(target):
            raise ValueError(
                f"Invalid transition: {self.status.value} → {target.value}"
            )
        self.status = target
        self.updated_at = now_iso()
        if target in _RESURRECTABLE:
            self.stall_cycles = 0
        return self

    _SPLIT_INVALID = {CardStatus.EXPIRED, CardStatus.DELEGATED}

    def split(self, titles: list[str],
              graph_cards: dict[str, Card] | None = None) -> list[Card]:
        """Split this card into child cards. Parent enters DELEGATED.

        If graph_cards is provided, cycle detection runs before creating children.
        """
        if self.status in self._SPLIT_INVALID:
            raise ValueError(
                f"Cannot split card in terminal status: {self.status.value}"
            )
        if graph_cards is not None and self.parent_card:
            if Card.detect_cycle(self.parent_card, graph_cards):
                raise ValueError(
                    f"Cycle detected: card {self.id} parent chain would loop"
                )
        self.status = CardStatus.DELEGATED
        self.updated_at = now_iso()
        children = []
        for title in titles:
            child = Card(
                source=self.source,
                source_id=self.source_id,
                title=title,
                priority=self.priority,
                assigned_to=self.assigned_to,
                assigned_by=self.assigned_by,
                team=self.team,
                parent_card=self.id,
                status=CardStatus.PROVIDED,
            )
            children.append(child)
        return children

    @staticmethod
    def detect_cycle(parent_id: str, graph_cards: dict[str, Card]) -> bool:
        """Check if adding parent_id → new card would create a cycle."""
        visited = set()
        current = parent_id
        while current:
            if current in visited:
                return True
            visited.add(current)
            card = graph_cards.get(current)
            current = card.parent_card if card else ""
        return False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "source": self.source, "source_id": self.source_id,
            "title": self.title, "priority": self.priority,
            "assigned_to": self.assigned_to, "assigned_by": self.assigned_by,
            "team": self.team, "status": self.status.value,
            "importance": self.importance, "urgency": self.urgency,
            "stall_cycles": self.stall_cycles,
            "oscillation_count": self.oscillation_count,
            "parent_card": self.parent_card,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_node(cls, props: dict) -> Card:
        return cls(
            id=props.get("id", ""),
            source=props.get("source", "cron"),
            source_id=props.get("source_id", ""),
            title=props.get("title", ""),
            priority=int(props.get("priority", 5)),
            assigned_to=props.get("assigned_to", ""),
            assigned_by=props.get("assigned_by", ""),
            team=props.get("team", ""),
            status=CardStatus(props.get("status", "provided")),
            importance=float(props.get("importance", 0.5)),
            urgency=float(props.get("urgency", 0.5)),
            stall_cycles=int(props.get("stall_cycles", 0)),
            oscillation_count=int(props.get("oscillation_count", 0)),
            parent_card=props.get("parent_card", ""),
            created_at=props.get("created_at", ""),
            updated_at=props.get("updated_at", ""),
        )


# ── Kanban board ────────────────────────────────────────────────────────

class KanbanBoard:
    """Per-role Kanban board backed by FalkorDB.

    Columns are implicit via Card.status:
      pending:  provided, ready
      in_progress: running, reviewing
      done:  done, failed, cancelled, expired, delegated, blocked
    """

    def __init__(self, projection: FalkorProjection, role: str, team: str):
        self._proj = projection
        self.role = role
        self.team = team

    def _ensure_board(self) -> str:
        """Idempotent board creation. Returns board node ID."""
        result = self._proj.g.query(
            "MERGE (b:Object {role:$role, team:$team}) "
            "ON CREATE SET b.id=$id, b.created_at=$ts "
            "SET b.objectKind = 'pm:kanbanBoard' "
            "RETURN b.id",
            params={"role": self.role, "team": self.team,
                    "id": ulid(), "ts": now_iso()},
        ).result_set
        return result[0][0] if result else ""

    def add_card(self, card: Card) -> None:
        """Add a card to this board."""
        d = card.to_dict()
        self._proj.g.query(
            "MERGE (c:Object {id:$id}) "
            "SET c.objectKind = 'pm:card', c += $props "
            "WITH c "
            "MATCH (b:Object {role:$role, team:$team}) "
            "MERGE (c)-[:ON_BOARD]->(b)",
            params={"id": card.id, "props": d,
                    "role": self.role, "team": self.team},
        )

    def pull_highest(self) -> Card | None:
        """Pull highest-priority pending card. Returns None if empty.

        Atomically claims the card by setting status→running so concurrent
        callers can't double-dispatch the same card.
        """
        results = self._proj.g.query(
            "MATCH (c:Object)-[:ON_BOARD]->(b:Object {role:$role, team:$team}) "
            "WHERE c.status IN ['provided','ready'] "
            "WITH c ORDER BY c.priority ASC, c.created_at ASC LIMIT 1 "
            "SET c.status = 'running', c.updated_at = $ts "
            "RETURN c",
            params={"role": self.role, "team": self.team, "ts": now_iso()},
        ).result_set
        if not results:
            return None
        return Card.from_node(dict(results[0][0].properties))

    def move_card(self, card_id: str, new_status: CardStatus) -> None:
        """Move a card to a new status column."""
        card = self._get_card(card_id)
        if card is None:
            raise ValueError(f"Card {card_id} not found on board")
        card.transition(new_status)
        d = card.to_dict()
        self._proj.g.query(
            "MATCH (c:Object {id:$id}) SET c.objectKind = 'pm:card', c += $props",
            params={"id": card_id, "props": d},
        )

    def redirect_card(self, card_id: str, target_role: str) -> None:
        """Redirect a card to another role's board.

        Raises ValueError if the target board does not exist or card is
        in a non-redirectable status (done, expired, delegated, cancelled).
        """
        # Verify source card exists and is redirectable
        card = self._get_card(card_id)
        if card is None:
            raise ValueError(f"Card {card_id} not found on board")
        if card.status in {CardStatus.DONE, CardStatus.EXPIRED, CardStatus.DELEGATED, CardStatus.CANCELLED}:
            raise ValueError(
                f"Cannot redirect card in terminal status: {card.status.value}"
            )
        # Verify target board exists before deleting source edge
        target_check = self._proj.g.query(
            "MATCH (b:Object {role:$target, team:$team}) RETURN b.role",
            params={"target": target_role, "team": self.team},
        ).result_set
        if not target_check:
            raise ValueError(
                f"Target board not found: role={target_role}, team={self.team}"
            )
        self._proj.g.query(
            "MATCH (c:Object {id:$id})-[r:ON_BOARD]->(old:Object) "
            "DELETE r "
            "WITH c "
            "MATCH (new:Object {role:$target, team:$team}) "
            "MERGE (c)-[:ON_BOARD]->(new) "
            "SET c.assigned_to=$target",
            params={"id": card_id, "target": target_role, "team": self.team},
        )

    def list_pending(self) -> list[Card]:
        """All pending cards on this board."""
        results = self._proj.g.query(
            "MATCH (c:Object)-[:ON_BOARD]->(b:Object {role:$role, team:$team}) "
            "WHERE c.status IN ['provided','ready'] "
            "RETURN c ORDER BY c.priority ASC",
            params={"role": self.role, "team": self.team},
        ).result_set
        return [Card.from_node(dict(r[0].properties)) for r in results]

    def _get_card(self, card_id: str) -> Card | None:
        results = self._proj.g.query(
            "MATCH (c:Object {id:$id})-[:ON_BOARD]->(b:Object {role:$role, team:$team}) "
            "RETURN c",
            params={"id": card_id, "role": self.role, "team": self.team},
        ).result_set
        if not results:
            return None
        return Card.from_node(dict(results[0][0].properties))


# ── Coordinator ──────────────────────────────────────────────────────────

class Coordinator:
    """S2 Coordination — reads Roadmap, dispatches cards to Kanban boards.

    Stateless: all coordination state lives in the FalkorDB graph.
    """

    def __init__(self, projection: FalkorProjection, stall_cycles: int = 3):
        self._proj = projection
        self.stall_cycles = stall_cycles

    def dispatch(self, roadmap_items: list[dict]) -> list[Card]:
        """Read Roadmap items → create cards → dispatch to boards.

        Differentiates strategist-level (S3-S5) from implementer-level (S1) work.
        Returns list of created cards.
        """
        cards: list[Card] = []
        for item in roadmap_items:
            card = Card(
                source=item.get("source", "cron"),
                source_id=item.get("id", ""),
                title=item.get("title", ""),
                priority=item.get("priority", 5),
                assigned_by="coordinator",
                team=item.get("team", ""),
                importance=item.get("importance", 0.5),
                urgency=item.get("urgency", 0.5),
            )
            # Classify: strategist (S3-S5) vs implementer (S1)
            role_type = item.get("role_type", "implementer")
            card.assigned_to = item.get(
                "role",
                f"{role_type}-{item.get('team','default')}"
            )
            # Conflict check before dispatch
            conflicts = self.detect_conflicts(card)
            if conflicts:
                card.status = CardStatus.BLOCKED
                card.title += f" [CONFLICT: {conflicts[0]['reason']}]"
            else:
                card.status = CardStatus.READY

            board = KanbanBoard(self._proj, card.assigned_to, card.team)
            board.add_card(card)
            cards.append(card)
        return cards

    def detect_stalls(self, team: str) -> list[Card]:
        """Find cards untouched for >N cycles."""
        results = self._proj.g.query(
            "MATCH (c:Object) WHERE c.team=$team "
            "AND c.status IN ['ready','running','reviewing'] "
            "AND c.stall_cycles > $max "
            "RETURN c ORDER BY c.stall_cycles DESC",
            params={"team": team, "max": self.stall_cycles},
        ).result_set
        stalled = [Card.from_node(dict(r[0].properties)) for r in results]
        # ponytail: best-effort increment — SELECT+SET not atomic across queries,
        # but SET c.stall_cycles = c.stall_cycles + 1 is atomic within its own query.
        # Full transactional guard needs BEGIN/COMMIT (FalkorDB doesn't support).
        for card in stalled:
            self._proj.g.query(
                "MATCH (c:Object {id:$id}) SET c.stall_cycles = c.stall_cycles + 1",
                params={"id": card.id},
            )
        return stalled

    def eisenhower_review(self, role: str, team: str = "") -> list[dict]:
        """Score and rank cards on a role's board.

        If team is provided, filters to that team; otherwise all teams.
        Returns recommendations: promote, deprioritize, archive.
        """
        if team:
            results = self._proj.g.query(
                "MATCH (c:Object)-[:ON_BOARD]->(b:Object {role:$role, team:$team}) "
                "WHERE c.status IN ['ready','running','reviewing'] "
                "RETURN c",
                params={"role": role, "team": team},
            ).result_set
        else:
            results = self._proj.g.query(
                "MATCH (c:Object)-[:ON_BOARD]->(b:Object {role:$role}) "
                "WHERE c.status IN ['ready','running','reviewing'] "
                "RETURN c",
                params={"role": role},
            ).result_set
        recommendations = []
        for r in results:
            card = Card.from_node(dict(r[0].properties))
            score = (card.importance + card.urgency) / 2
            if score < 0.3:
                rec = "archive"
            elif score < 0.6:
                rec = "deprioritize"
            else:
                rec = "promote"
            recommendations.append({
                "card_id": card.id,
                "title": card.title,
                "score": round(score, 3),
                "recommendation": rec,
            })
        # Sort highest priority first
        recommendations.sort(key=lambda x: x["score"], reverse=True)
        return recommendations

    def detect_conflicts(self, card: Card) -> list[dict]:
        """Check for resource/goal/priority conflicts.

        Returns list of conflicts found. Empty list = no conflict.
        Anti-oscillation: tracks rapid toggling between assignees.
        """
        conflicts = []
        # Resource conflict: same source_id in running/reviewing on any board
        if card.source_id:
            results = self._proj.g.query(
                "MATCH (c:Object {source_id:$sid}) "
                "WHERE c.status IN ['running','reviewing'] "
                "AND c.id <> $cid "
                "RETURN c.assigned_to, c.title",
                params={"sid": card.source_id, "cid": card.id},
            ).result_set
            for r in results:
                conflicts.append({
                    "type": "resource",
                    "card_id": card.id,
                    "conflicting_with": r[0],
                    "reason": f"Same source already running by {r[0]}: {r[1]}",
                })

        # Anti-oscillation: check for rapid reassignment pattern
        # ponytail: counter-based with status gate — ≥3 oscillations on active
        # cards suppresses dispatch; resolved cards don't gate new work forever
        if card.source_id:
            prev = self._proj.g.query(
                "MATCH (c:Object {source_id:$sid}) "
                "WHERE c.oscillation_count >= 3 "
                "AND c.status IN ['provided','ready','running','reviewing','blocked'] "
                "RETURN c.id",
                params={"sid": card.source_id},
            ).result_set
            if prev:
                conflicts.append({
                    "type": "oscillation",
                    "card_id": card.id,
                    "reason": "Anti-oscillation: ≥3 prior reassignments for this source",
                })

        return conflicts
