"""Board CLI — agent card workflow via Kanban board.

Usage:
  tortoise board pull <role> [--team <team>]    — claim highest-priority card
  tortoise board complete <card-id>              — mark card done
  tortoise board fail <card-id> --reason "..."   — mark card failed
  tortoise board list [--status <s>] [--role <r>] [--team <t>]  — list cards
  tortoise board status [--role <r>] [--team <t>]  — board overview
  tortoise board handoff <card-id> --to <role> --summary "..." --remaining "..."

The board is the coordination surface — agents pull, work, handoff.
No Slack needed. ONTOLOGY_v2.5 §1.1, PM domain extension.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from coordination import Card, CardStatus, KanbanBoard
from projection import FalkorProjection

DB_PATH = str(_PROJECT_ROOT / "tortoise.db")


def _get_board(role: str, team: str = "default") -> KanbanBoard:
    proj = FalkorProjection(DB_PATH)
    return KanbanBoard(proj, role, team)


# ── Commands ──────────────────────────────────────────────────

def cmd_pull(role: str, team: str = "default") -> None:
    """Claim the highest-priority pending card for a role."""
    board = _get_board(role, team)
    card = board.pull_highest()
    if card is None:
        print(f"No pending cards for {role}")
        return

    print(f"Claimed: {card.title}")
    print(f"  Card ID:  {card.id}")
    print(f"  Status:   {card.status.value}")
    print(f"  Priority: {card.priority}")
    print(f"  Source:   {card.source} ({card.source_id})")
    if card.source == "mission":
        print(f"  Mission:  {card.source_id}")


def cmd_complete(card_id: str, role: str = "", team: str = "default") -> None:
    """Mark a card as completed."""
    if not role:
        print("Error: --role required to find the card board")
        sys.exit(1)
    board = _get_board(role, team)
    try:
        board.move_card(card_id, CardStatus.DONE)
        print(f"Completed: {card_id}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_fail(card_id: str, reason: str, role: str = "", team: str = "default") -> None:
    """Mark a card as failed with a reason."""
    if not role:
        print("Error: --role required")
        sys.exit(1)
    board = _get_board(role, team)
    try:
        board.move_card(card_id, CardStatus.FAILED)
        print(f"Failed: {card_id}")
        print(f"  Reason: {reason}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_list(status: str = "", role: str = "", team: str = "default") -> None:
    """List cards on a board, optionally filtered by status."""
    if not role:
        print("Error: --role required")
        sys.exit(1)
    board = _get_board(role, team)

    if status:
        try:
            s = CardStatus(status)
        except ValueError:
            print(f"Invalid status: {status}")
            print(f"Valid: {[s.value for s in CardStatus]}")
            sys.exit(1)
        # ponytail: filter manually since KanbanBoard doesn't have status-filtered list
        cards = [c for c in board.list_pending()] if status == "pending" else []
        if status != "pending":
            all_cards = board.list_pending()  # list_pending returns provided+ready
            cards = [c for c in all_cards if c.status.value == status]
    else:
        cards = board.list_pending()

    if not cards:
        print(f"No cards for {role}" + (f" (status: {status})" if status else ""))
        return

    print(f"{'ID':<20} {'PRI':<5} {'STATUS':<12} {'TITLE'}")
    print("-" * 70)
    for c in sorted(cards, key=lambda c: c.priority):
        short_id = c.id[:18] if len(c.id) > 18 else c.id
        print(f"{short_id:<20} {c.priority:<5} {c.status.value:<12} {c.title[:40]}")


def cmd_status(role: str = "", team: str = "default") -> None:
    """Show board overview — cards per column, stalled items."""
    proj = FalkorProjection(DB_PATH)

    if role:
        roles = [(role, team)]
    else:
        # Discover all roles with boards
        result = proj.g.query(
            "MATCH (b:Object {objectKind: 'pm:kanbanBoard'}) "
            "RETURN b.role, b.team"
        ).result_set
        roles = [(r[0], r[1]) for r in result] if result else []

    if not roles:
        print("No boards found. Create missions first.")
        return

    for r, t in roles:
        board = KanbanBoard(proj, r, t)
        pending = len(board.list_pending())

        # Count by status via multiple queries (ponytail: single Cypher in V2)
        running = len(proj.g.query(
            "MATCH (c:Object {objectKind: 'pm:card'})-[:ON_BOARD]->"
            "(b:Object {objectKind: 'pm:kanbanBoard', role: $r, team: $t}) "
            "WHERE c.status = 'running' RETURN c",
            params={"r": r, "t": t}
        ).result_set)

        reviewing = len(proj.g.query(
            "MATCH (c:Object {objectKind: 'pm:card'})-[:ON_BOARD]->"
            "(b:Object {objectKind: 'pm:kanbanBoard', role: $r, team: $t}) "
            "WHERE c.status = 'reviewing' RETURN c",
            params={"r": r, "t": t}
        ).result_set)

        done = len(proj.g.query(
            "MATCH (c:Object {objectKind: 'pm:card'})-[:ON_BOARD]->"
            "(b:Object {objectKind: 'pm:kanbanBoard', role: $r, team: $t}) "
            "WHERE c.status = 'done' RETURN c",
            params={"r": r, "t": t}
        ).result_set)

        print(f"\n{r} ({t}):")
        print(f"  pending: {pending}  running: {running}  reviewing: {reviewing}  done: {done}")


def cmd_handoff(
    card_id: str, to_role: str, summary: str, remaining: str,
    from_role: str = "", team: str = "default",
) -> None:
    """Complete a card and create a handoff to another role."""
    if not from_role:
        print("Error: --from-role required")
        sys.exit(1)

    # Complete the current card
    board = _get_board(from_role, team)
    try:
        board.move_card(card_id, CardStatus.DONE)
    except ValueError as e:
        print(f"Error completing card: {e}")
        sys.exit(1)

    # Create handoff (import from eldato repo)
    import importlib
    try:
        from handoff import Handoff
    except ImportError:
        print("Error: handoff.py not available. Ensure operations/coordination/ is on PYTHONPATH.")
        sys.exit(1)

    h = Handoff(
        handoff_id=f"ho-{int(time.monotonic_ns())}",
        from_role=from_role,
        to_role=to_role,
        from_team=team,
        to_team=team,
        mission_id="",  # ponytail: extract from card source_id
        summary=summary,
        remaining=remaining,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    if not h.is_valid():
        print("Error: Invalid handoff — missing required fields")
        sys.exit(1)

    print(f"Handoff created: {h.handoff_id}")
    print(f"  From: {from_role} → To: {to_role}")
    print(f"  Summary: {summary[:60]}...")
    print(f"  Next: tortoise board pull {to_role}")


# ── CLI ───────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Tortoise board — agent card workflow")
    sub = ap.add_subparsers(dest="command")

    # pull
    pp = sub.add_parser("pull", help="Claim highest-priority card")
    pp.add_argument("role", help="Role to pull for")
    pp.add_argument("--team", default="default")

    # complete
    cp = sub.add_parser("complete", help="Mark card as done")
    cp.add_argument("card_id", help="Card ID")
    cp.add_argument("--role", default="", help="Role that owns the card")
    cp.add_argument("--team", default="default")

    # fail
    fp = sub.add_parser("fail", help="Mark card as failed")
    fp.add_argument("card_id", help="Card ID")
    fp.add_argument("--reason", default="", help="Failure reason")
    fp.add_argument("--role", default="", help="Role that owns the card")
    fp.add_argument("--team", default="default")

    # list
    lp = sub.add_parser("list", help="List cards")
    lp.add_argument("--status", default="", help="Filter by status")
    lp.add_argument("--role", default="", help="Role board")
    lp.add_argument("--team", default="default")

    # status
    sp = sub.add_parser("status", help="Board overview")
    sp.add_argument("--role", default="", help="Filter to one role")
    sp.add_argument("--team", default="default")

    # handoff
    hp = sub.add_parser("handoff", help="Complete card and handoff to another role")
    hp.add_argument("card_id", help="Card ID")
    hp.add_argument("--to", dest="to_role", required=True, help="Target role")
    hp.add_argument("--from-role", default="", help="Current role")
    hp.add_argument("--summary", required=True, help="What was done")
    hp.add_argument("--remaining", required=True, help="What remains")
    hp.add_argument("--team", default="default")

    args = ap.parse_args(argv)

    if args.command == "pull":
        cmd_pull(args.role, args.team)
    elif args.command == "complete":
        cmd_complete(args.card_id, args.role, args.team)
    elif args.command == "fail":
        cmd_fail(args.card_id, args.reason, args.role, args.team)
    elif args.command == "list":
        cmd_list(args.status, args.role, args.team)
    elif args.command == "status":
        cmd_status(args.role, args.team)
    elif args.command == "handoff":
        cmd_handoff(
            args.card_id, args.to_role, args.summary, args.remaining,
            getattr(args, 'from_role', ''), args.team,
        )
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
