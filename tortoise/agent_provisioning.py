"""Agent Provisioning — 4-layer capability model for role-based agent context.

ARCH-007 (EP 0.803). BUILD not buy — no OPA/Casbin.
Extends Pi's YAML-frontmatter subagent format with capability layers.

4-layer model:
  Layer 0: Global Deny — base deny-all, then allowlist
  Layer 1: Tools (incl MCP) — tool + MCP server allowlist
  Layer 2: Skills — skill allowlist
  Layer 3: Memory Filter — storage-agnostic MemoryScope contract

Failure recovery: timeout 60s, retry 3x, dead letter, circuit breaker.

Dependencies:
  - #6876 (MemoryScope) — tortoise.memory_scope.MemoryScope Protocol
  - #6875 (Skill Declaration) — operations/tools/skill_declaration.py
  - #6871 (State Machine Core) — enforcement (converging)
"""
from __future__ import annotations

import dataclasses
import enum
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

# ── Data model ───────────────────────────────────────────────────────────


@dataclasses.dataclass
class Capabilities:
    tools: list[str] = dataclasses.field(default_factory=list)
    mcp: list[str] = dataclasses.field(default_factory=list)
    skills: list[str] = dataclasses.field(default_factory=list)
    memory_filter: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Role:
    team: str
    role: str
    capabilities: Capabilities = dataclasses.field(default_factory=Capabilities)
    deny: list[str] = dataclasses.field(default_factory=list)
    source_path: str = ""


# ── Circuit breaker ──────────────────────────────────────────────────────


class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerError(Exception):
    """Raised when circuit is OPEN — caller should fail fast or use fallback."""


class CircuitBreaker:
    """Stdlib-only circuit breaker. Thread-safe via RLock."""

    def __init__(self, failure_threshold: int = 3, timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.state = CircuitState.CLOSED
        self._lock = threading.RLock()

    def _should_attempt_reset(self) -> bool:
        if self.state != CircuitState.OPEN:
            return False
        return time.time() - self.last_failure_time > self.timeout

    def call(self, func, *args, **kwargs) -> Any:
        with self._lock:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerError("Circuit is OPEN")
        try:
            result = func(*args, **kwargs)
            with self._lock:
                self._on_success()
            return result
        except Exception:
            with self._lock:
                self._on_failure()
            raise

    def _on_success(self) -> None:
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        # ponytail: single HALF_OPEN failure → immediate OPEN.
        # Prevents race where concurrent probes absorb each other's failures.
        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN


# ── Dead letter ──────────────────────────────────────────────────────────

_dead_letter: list[dict[str, Any]] = []
_dead_letter_lock = threading.Lock()


def dead_letter_log(operation: str, error: str, **ctx) -> None:
    with _dead_letter_lock:
        _dead_letter.append({"operation": operation, "error": error, "ctx": ctx, "ts": time.time()})


def dead_letter_drain() -> list[dict[str, Any]]:
    with _dead_letter_lock:
        drained = list(_dead_letter)
        _dead_letter.clear()
    return drained


# ── Manifest loading ─────────────────────────────────────────────────────


def _extract_frontmatter(text: str) -> dict[str, Any] | None:
    """Extract YAML frontmatter dict, or None if missing/malformed."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = 1
    while end < len(lines) and lines[end].strip() != "---":
        end += 1
    if end >= len(lines):
        return None
    parsed = yaml.safe_load("\n".join(lines[1:end]))
    return parsed if isinstance(parsed, dict) else None


def load_role(manifest_path: str) -> Role:
    """Load a role manifest from a .md file with YAML frontmatter."""
    path = Path(manifest_path)
    raw = path.read_text(encoding="utf-8")
    fm = _extract_frontmatter(raw)
    if not fm:
        raise ValueError(f"No YAML frontmatter in {manifest_path}")
    caps_raw = fm.get("capabilities", {})
    return Role(
        team=fm.get("team", ""),
        role=fm.get("role", ""),
        capabilities=Capabilities(
            tools=caps_raw.get("tools", []),
            mcp=caps_raw.get("mcp", []),
            skills=caps_raw.get("skills", []),
            memory_filter=caps_raw.get("memory_filter", {}),
        ),
        deny=fm.get("deny", []),
        source_path=str(path),
    )


# ── Validation ───────────────────────────────────────────────────────────


def validate_role(role: Role) -> list[str]:
    """Validate a Role. Returns list of error strings (empty = valid)."""
    errors: list[str] = []
    if not role.team:
        errors.append("Missing required field: team")
    if not role.role:
        errors.append("Missing required field: role")
    if not isinstance(role.capabilities.tools, list):
        errors.append("capabilities.tools must be a list")
    if not isinstance(role.capabilities.mcp, list):
        errors.append("capabilities.mcp must be a list")
    if not isinstance(role.capabilities.skills, list):
        errors.append("capabilities.skills must be a list")
    if not isinstance(role.deny, list):
        errors.append("deny must be a list")
    # ponytail: deny supersedes allow — warn on conflicts (skip if non-list)
    if isinstance(role.deny, list):
        denied = set(role.deny)
        for layer, items in [("tools", role.capabilities.tools), ("mcp", role.capabilities.mcp), ("skills", role.capabilities.skills)]:
            if not isinstance(items, list):
                continue
            for item in items:
                if item in denied:
                    errors.append(f"'{item}' in both capabilities.{layer} and deny")
    return errors


# ── Context injection ────────────────────────────────────────────────────


def inject_context(role: Role) -> dict[str, Any]:
    """Build agent context dict from a Role for prompt injection.

    Returns a dict suitable for merging into Pi's subagent config or
    passing to the Memory Orchestrator's MemoryScope.filter().
    """
    caps = role.capabilities
    return {
        "role": f"{role.team}/{role.role}",
        "allowlist": {
            "tools": caps.tools,
            "mcp_servers": caps.mcp,
            "skills": caps.skills,
        },
        "denylist": role.deny,
        "memory_scope": caps.memory_filter,
    }


# ── Retry + Timeout ─────────────────────────────────────────────────────


def retry_call(
    func,
    *args,
    breaker: CircuitBreaker | None = None,
    max_retries: int = 3,
    timeout: float = 60.0,
    operation: str = "unknown",
    **kwargs,
) -> Any:
    """Call func with timeout, retry, circuit breaker, and dead letter.

    Uses daemon thread + join(timeout) instead of ThreadPoolExecutor to
    avoid shutdown deadlock on timeout (#6925). On exhaustion: logs to
    dead letter, re-raises last exception.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        result: list[Any] = []
        exc_info: list[Exception] = []

        def _target() -> None:
            try:
                if breaker is not None:
                    result.append(breaker.call(func, *args, **kwargs))
                else:
                    result.append(func(*args, **kwargs))
            except Exception as e:
                exc_info.append(e)

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            # ponytail: daemon thread — dies with process, no resource leak
            last_exc = TimeoutError(f"Operation timed out after {timeout}s: {operation}")
            if attempt < max_retries - 1:
                continue
            break

        if exc_info:
            exc = exc_info[0]
            if isinstance(exc, CircuitBreakerError):
                raise exc
            last_exc = exc
            if attempt < max_retries - 1:
                continue
            break

        return result[0]

    # Exhausted
    dead_letter_log(operation, str(last_exc), retries=max_retries, timeout=timeout)
    raise last_exc  # type: ignore[misc]


# ── CI CLI ───────────────────────────────────────────────────────────────


def validate_all_manifests(agents_dir: str) -> tuple[int, list[str]]:
    """Validate all role manifests in an agents directory.

    Returns (error_count, error_messages). Exit 0 = clean.
    """
    base = Path(agents_dir)
    if not base.is_dir():
        return 1, [f"Directory not found: {agents_dir}"]
    errors: list[str] = []
    for md_file in sorted(base.rglob("*.md")):
        try:
            role = load_role(str(md_file))
            role_errors = validate_role(role)
            for e in role_errors:
                errors.append(f"{md_file}: {e}")
        except Exception as exc:
            errors.append(f"{md_file}: {exc}")
    return len(errors), errors


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for CI validation."""
    args = argv or sys.argv[1:]
    if not args:
        print("Usage: python -m tortoise.agent_provisioning [--validate DIR]", file=sys.stderr)
        return 1
    if args[0] == "--validate":
        agents_dir = args[1] if len(args) > 1 else "operations/agents"
        count, msgs = validate_all_manifests(agents_dir)
        for m in msgs:
            print(m, file=sys.stderr)
        if count:
            print(f"\n{count} validation error(s)", file=sys.stderr)
        return 1 if count else 0
    print(f"Unknown command: {args[0]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
