"""Single-machine concurrency: fcntl.flock for JSONL, atomic os.rename for cards.

Zero dependencies. Pessimistic locking (research brief §5: abort costs for AI
agents are minutes/dollars, not microseconds).
"""

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Optional


def locked_append(path: Path, record: dict[str, Any],
                  timeout_ms: float = 5000.0,
                  dedup_key: str | None = None) -> bool:
    """Append a JSON line to a file under an advisory flock.

    Blocks up to timeout_ms. Returns True on success, False if timeout.
    If dedup_key is provided, the file is scanned under lock — if any
    existing record contains that key:value, the write is skipped (returns True).

    Per research brief §5: pessimistic locking is correct for AI agents
    whose abort costs are measured in minutes and dollars.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_ms / 1000.0
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.005)  # ponytail: 5ms spin — good enough for ≤10 agents

        # Dedup check under lock (no TOCTOU)
        if dedup_key:
            os.lseek(fd, 0, os.SEEK_SET)
            raw = os.read(fd, 4096 * 1024).decode('utf-8', errors='replace')
            for line in raw.split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if ev.get('event_id') == dedup_key:
                        return True  # already exists, skip
                except json.JSONDecodeError:
                    continue
        
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        os.write(fd, line.encode())
        return True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_claim(card_dir: Path, card_id: str, claim_data: dict[str, Any],
                 suffix: str = ".card") -> Optional[Path]:
    """Atomically claim a card using O_EXCL (no TOCTOU window).

    Uses open(target, 'x') for atomic create-or-fail. If the target already
    exists, another agent claimed it first — returns None.

    Returns the Path of the claimed card file, or None if already claimed.
    """
    card_dir.mkdir(parents=True, exist_ok=True)
    target = card_dir / f"{card_id}{suffix}"
    try:
        # O_CREAT | O_EXCL — atomic, fails if exists (no TOCTOU)
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, json.dumps(claim_data, ensure_ascii=False, default=str).encode())
        finally:
            os.close(fd)
        return target
    except FileExistsError:
        return None


# --- self-check ---
if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())

    # locked_append
    log = d / "test.jsonl"
    ok = locked_append(log, {"event": "test", "n": 1})
    assert ok
    assert log.exists()
    lines = log.read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "test"

    # atomic_claim — first claim succeeds
    cards = d / "cards"
    claimed = atomic_claim(cards, "card-001", {"owner": "agent-1"})
    assert claimed is not None
    assert claimed.exists()
    data = json.loads(claimed.read_text())
    assert data["owner"] == "agent-1"

    # atomic_claim — second claim on same ID returns None
    claimed2 = atomic_claim(cards, "card-001", {"owner": "agent-2"})
    assert claimed2 is None

    print("✅ concurrency")
    import shutil; shutil.rmtree(d)
