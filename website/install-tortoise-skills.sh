#!/usr/bin/env bash
# install-tortoise-skills.sh — install the official Tortoise agent skills.
#
# The skills are downloaded from the Tortoise product site
# (https://app.premiselabs.co/skills/<name>/SKILL.md) — no git clone, no
# third-party repo. The source of truth is the public repo:
# https://github.com/daniel-ospina/tortoise-skills-and-integrations
#
# Project-scoped for Claude Code / Codex / Cursor (installs into the current
# project's skills dir — version-controllable, non-destructive to the
# machine); personal for Pi (~/.pi/agent/skills — the only supported path).
#
# Usage:
#   curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness claude
#   (or codex | cursor | pi)
#
# Idempotent: re-running updates the skills in place. Prints the verify step.
set -euo pipefail

SKILLS_VERSION="v1"   # bump when the skill set changes
SKILLS_BASE="https://app.premiselabs.co/skills"
SKILLS=(how-to-use-tortoise tortoise-decide tortoise-file-finding)

HARNESS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --harness) HARNESS="${2:-}"; shift 2 ;;
    -h|--help) sed -n '1,20p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$HARNESS" ]; then
  echo "Usage: install-tortoise-skills.sh --harness claude|codex|cursor|pi" >&2
  exit 2
fi

case "$HARNESS" in
  claude) DEST=".claude/skills" ;;
  codex)  DEST=".codex/skills" ;;
  cursor) DEST=".cursor/skills" ;;
  pi)     DEST="$HOME/.pi/agent/skills" ;;
  *) echo "Unknown harness: $HARNESS (expected claude|codex|cursor|pi)" >&2; exit 2 ;;
esac

echo "Installing Tortoise skills (${SKILLS_VERSION}) into: $DEST"
mkdir -p "$DEST"

for s in "${SKILLS[@]}"; do
  mkdir -p "$DEST/$s"
  if curl -fsSL "$SKILLS_BASE/$s/SKILL.md" -o "$DEST/$s/SKILL.md" 2>/dev/null; then
    echo "  ✓ $s"
  else
    echo "  ✗ $s — download failed from $SKILLS_BASE/$s/SKILL.md" >&2
    exit 1
  fi
done

# Verify the target dir — we KNOW where we wrote, so this is a local check.
missing=()
for s in "${SKILLS[@]}"; do
  [ -s "$DEST/$s/SKILL.md" ] || missing+=("$s")
done

if [ ${#missing[@]} -eq 0 ]; then
  echo ""
  echo "✅ Tortoise skills installed to $DEST"
  echo "   ${SKILLS[*]}"
  echo ""
  echo "Next: restart your agent, then confirm the skills are listed:"
  case "$HARNESS" in
    claude) echo "   claude — the skills appear under /skills" ;;
    codex)  echo "   codex — check the skills list in the agent" ;;
    cursor) echo "   cursor — skills load from .cursor/skills" ;;
    pi)     echo "   pi — ~/.pi/agent/skills is scanned on startup" ;;
  esac
else
  echo ""
  echo "⚠️  Some skills did not verify in $DEST: ${missing[*]}" >&2
  echo "   Check the directory + permissions, then re-run the installer." >&2
  exit 1
fi
