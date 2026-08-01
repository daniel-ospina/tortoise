#!/usr/bin/env python3
"""
review.py — Review queue CLI for unmatched speakers.

Lists speakers that the bridge couldn't match to Twenty contacts,
lets Danny resolve them manually on his own schedule.

Usage:
    python3 review.py              # List queued speakers
    python3 review.py --resolve    # Interactive resolution
    python3 review.py --clear      # Clear the queue
"""
import json
import os
import sys
from pathlib import Path

REVIEW_QUEUE_PATH = os.path.join(str(Path.home()), ".minutes", "review_queue.json")


def load_queue() -> list:
    path = Path(REVIEW_QUEUE_PATH)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return []


def save_queue(queue: list) -> None:
    path = Path(REVIEW_QUEUE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, indent=2))


def list_queue():
    queue = load_queue()
    if not queue:
        print("✅ No pending reviews.")
        return

    print(f"📋 {len(queue)} unmatched speaker(s):\n")
    for i, item in enumerate(queue):
        print(f"  [{i}] {item['speaker_name']}")
        print(f"      Meeting: {item['meeting_id']}")
        print(f"      Segments: {item.get('segments', '?')}")
        print(f"      Queued: {item.get('queued_at', 'unknown')}")
        if item.get("resolved_at"):
            print(f"      Resolved: {item.get('email', item.get('resolved_name', '?'))}")
        print()


def resolve():
    queue = load_queue()
    if not queue:
        print("✅ No pending reviews.")
        return

    unresolved = [item for item in queue if not item.get("resolved_at")]
    if not unresolved:
        print("✅ All speakers are already resolved.")
        return

    print(f"Resolving {len(unresolved)} unmatched speaker(s)...\n")

    for item in unresolved:
        print(f"Speaker: {item['speaker_name']}")
        print(f"  Meeting: {item['meeting_id']}")
        print(f"  Options: [e] provide email  [n] provide name  [s] skip  [q] quit")
        choice = input("  > ").strip().lower()

        if choice == 'q':
            break
        elif choice == 'e':
            email = input("  Email: ").strip()
            item['email'] = email
            item['resolved_at'] = __import__('time').strftime(
                "%Y-%m-%dT%H:%M:%SZ", __import__('time').gmtime()
            )
            print(f"  ✅ Resolved with email: {email}")
        elif choice == 'n':
            name = input("  Full name: ").strip()
            item['resolved_name'] = name
            item['resolved_at'] = __import__('time').strftime(
                "%Y-%m-%dT%H:%M:%SZ", __import__('time').gmtime()
            )
            print(f"  ✅ Resolved with name: {name}")
        elif choice == 's':
            print("  ⏭️  Skipped")
        print()

    # Save resolved items back to queue (bridge.py consumes them)
    save_queue(queue)

    remaining = len([i for i in queue if not i.get("resolved_at")])
    resolved_count = len([i for i in queue if i.get("resolved_at")])
    if resolved_count:
        print(f"✅ {resolved_count} speaker(s) resolved. {remaining} remaining.")
        print("   Bridge will apply resolutions on next meeting file it processes.")
    else:
        print(f"No changes. {remaining} speaker(s) still queued.")


if __name__ == "__main__":
    if "--clear" in sys.argv:
        save_queue([])
        print("✅ Review queue cleared.")
    elif "--resolve" in sys.argv:
        resolve()
    else:
        list_queue()
