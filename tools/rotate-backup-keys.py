#!/usr/bin/env python3
"""Operator rotation automation for backup-encryption keys (#2318).

Backup keys live in a managed secret store with a DUAL-KEY overlap window:
rotate() mints a NEW active (encrypt) key while the old active key is
RETAINED (env ``*_PREVIOUS`` var or an older file-store version) so archives
encrypted under the old key stay decryptable in-app during the window. After
the overlap the operator purges the retained key.

This tool automates that run (docs/ops/registry-backup-dr.md §Rotation):

  env store (Fly/GH secrets — the tool cannot mutate remote secrets, so it
  EMITS the exact assignments; the operator applies them out-of-band):
      uv run python tools/rotate-backup-keys.py --role registry_stream --emit-commands --fly-app tortoise-y4mjjq
      # ... then run the printed `fly deploy` to apply the staged secrets

  file store (selfhost — rotation applies in place, atomic 0600 write):
      uv run python tools/rotate-backup-keys.py --role backup --store file --path ~/.tortoise/backup-keys.json

  purge the retained key after the overlap window:
      uv run python tools/rotate-backup-keys.py --role registry_stream --purge --emit-commands --fly-app tortoise-y4mjjq

Old-archive decrypt verification (the rotation run's proof): pass a
pre-rotation dump.enc via --verify-dump and the tool decrypts it with the
post-rotation candidates, reporting the matching fingerprint.

No key material is printed unless --emit-commands (needed to apply). Default
output shows 8-hex fingerprints only — the never-log-key-material rule.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap() -> None:
    """Make ``tortoise`` importable when run as a bare script (uv run)."""
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)


_bootstrap()
from tortoise import secret_store as ss  # noqa: E402


def _render_commands(plan: ss.EnvRotationPlan, *, app: str | None) -> list[str]:
    """Exact env/secret commands for an env-store rotation plan."""
    if app:
        pairs = " ".join(
            f"{name}={value}" for name, value in plan.assignments.items() if value is not None
        )
        return [
            f"fly secrets set {pairs} --app {app}",
            f"fly deploy --app {app}   # pick up the new secret values",
        ]
    return [
        f"export {name}={shlex.quote(value)}"
        for name, value in plan.assignments.items()
        if value is not None
    ] + ["# add the export lines above to your env / .env, then restart the service"]


def _verify_dump(path: str, candidates: tuple[bytes, ...]) -> str:
    """Decrypt a pre-rotation dump.enc with post-rotation candidates; returns
    the fingerprint that authenticated ('' = none)."""
    from tortoise.hosted_backup import decrypt_backup

    blob = Path(path).read_bytes()
    for candidate in candidates:
        try:
            decrypt_backup(blob, key=candidate)
            return ss.key_fingerprint(candidate)
        except ValueError:
            continue
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="rotate-backup-keys",
        description="Rotate a backup-encryption key with dual-key retention (#2318).",
    )
    ap.add_argument(
        "--role", required=True, choices=sorted(ss.ROLES), help="encryption surface to rotate"
    )
    ap.add_argument(
        "--store",
        choices=("env", "file"),
        default=None,
        help="store provider (default: $BACKUP_KEY_STORE or 'env')",
    )
    ap.add_argument(
        "--path", default=None, help="file-store path (default: $BACKUP_KEY_STORE_PATH)"
    )
    ap.add_argument(
        "--purge",
        action="store_true",
        help="drop the retained key instead of rotating (post-overlap)",
    )
    ap.add_argument(
        "--fly-app", default=None, help="Fly app name — emit `fly secrets set/unset` commands"
    )
    ap.add_argument(
        "--verify-dump",
        default=None,
        metavar="PATH",
        help="pre-rotation dump.enc to prove retained-key decryption",
    )
    ap.add_argument(
        "--emit-commands",
        action="store_true",
        help="print the secret VALUES in the emitted commands",
    )
    args = ap.parse_args(argv)

    role = ss.role(args.role)
    store_name = args.store or os.environ.get(ss.PROVIDER_ENV, "env").strip() or "env"
    try:
        store = ss.open_key_store(store_name, path=args.path)
    except ss.KeyStoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.purge:
        if isinstance(store, ss.FileKeyStore):
            dropped = store.purge_retained(role.name)
            print(
                f"purged retained {role.name} keys: "
                + (", ".join(dropped) if dropped else "(none retained)")
            )
            active = store.active(role.name)
            print(
                f"active now: {ss.key_fingerprint(active)}"
                if active
                else f"{role.name} has no active key"
            )
        else:
            if not args.fly_app:
                print(
                    "error: env-store purge needs --fly-app (the retained "
                    "key lives in Fly secrets — this tool cannot unset it "
                    "without the app name)",
                    file=sys.stderr,
                )
                return 2
            print(
                f"purge {role.name}: unset the retained key "
                f"{role.previous_env} (active {role.active_env} unchanged, "
                f"fp {ss.key_fingerprint(store.active(role.name)) if store.active(role.name) else '(none)'})"
            )
            print(f"fly secrets unset {role.previous_env} --app {args.fly_app}")
            print(f"fly deploy --app {args.fly_app}   # pick up the secret change")
        return 0

    # ── rotation ─────────────────────────────────────────────────────────────
    if isinstance(store, ss.FileKeyStore):
        result = store.rotate(role.name)
        print(
            f"rotated {role.name} in store {store._path}: "
            f"new active {result.new_fingerprint}"
            + (
                f" (old active retained: {ss.key_fingerprint(result.old_active)})"
                if result.old_active
                else " (first key — no retained version)"
            )
            + (
                f"; oldest dropped: {', '.join(result.dropped_fingerprints)}"
                if result.dropped_fingerprints
                else ""
            )
        )
        candidates = store.candidates(role.name)
    else:
        plan = ss.plan_env_rotation(role.name)
        print(f"rotation plan for {role.name} ({role.active_env} / {role.previous_env}):")
        print(f"  new active : {plan.new_fingerprint}")
        old = plan.old_active_fingerprint or "(none — first key)"
        print(f"  old active : {old}  → retained for decryption")
        commands = _render_commands(plan, app=args.fly_app)
        if args.emit_commands:
            print("\n".join(commands))
        else:
            for name, value in plan.assignments.items():
                print(
                    f"  {name} = {ss.key_fingerprint(ss.decode_key(value)) if value is not None else '(unset)'}"
                )
            print(
                "\nsecret VALUES are redacted — rerun with --emit-commands to "
                "print the exact fly secrets set / export commands to apply."
            )
        candidates = (plan.new_key,) + ((plan.old_active,) if plan.old_active is not None else ())

    if args.verify_dump:
        fp = _verify_dump(args.verify_dump, candidates)
        if fp:
            print(f"verify-dump OK — {Path(args.verify_dump).name} decrypts with retained key {fp}")
        else:
            print(
                f"verify-dump FAILED — {args.verify_dump} does not decrypt "
                "with any post-rotation candidate",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
