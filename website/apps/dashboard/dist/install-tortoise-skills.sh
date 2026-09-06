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

SKILLS_VERSION="v2"   # bump when the skill set changes
SKILLS_BASE="https://app.premiselabs.co/skills"
# v2 (#1998 W2): +tortoise-onboarding — the ONE live onboarding script
# (successor to AGENT_ONBOARDING.md, archived M8). The installer is the
# distribution path the dashboard's universal command relies on.
SKILLS=(how-to-use-tortoise tortoise-decide tortoise-file-finding tortoise-onboarding)

usage() {
  cat <<'HELP'
Usage: install-tortoise-skills.sh --harness claude|codex|cursor|pi

Installs the official Tortoise skills into the harness's skills directory:
  claude -> .claude/skills    (project)
  codex  -> .agents/skills    (project)  — Codex's documented skill root (#2329);
                                       also scanned at ~/.agents/skills (personal)
  cursor -> .cursor/skills   (project)
  pi     -> ~/.pi/agent/skills (personal)

Run: curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness <harness>
HELP
  exit 0
}

HARNESS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --harness) [ $# -ge 2 ] || { echo "Missing value for --harness" >&2; exit 2; }
               HARNESS="${2}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$HARNESS" ]; then
  echo "Usage: install-tortoise-skills.sh --harness claude|codex|cursor|pi" >&2
  exit 2
fi

case "$HARNESS" in
  claude) DEST=".claude/skills" ;;
  codex)  DEST=".agents/skills" ;;
  cursor) DEST=".cursor/skills" ;;
  pi)     DEST="$HOME/.pi/agent/skills" ;;
  *) echo "Unknown harness: $HARNESS (expected claude|codex|cursor|pi)" >&2; exit 2 ;;
esac

echo "Installing Tortoise skills (${SKILLS_VERSION}) into: $DEST"
mkdir -p "$DEST"

for s in "${SKILLS[@]}"; do
  mkdir -p "$DEST/$s"
  tmp="$DEST/$s/SKILL.md.tmp"
  if curl -fsSL --max-time 20 "$SKILLS_BASE/$s/SKILL.md" -o "$tmp" \
      && grep -q "^name: $s$" "$tmp"; then
    mv "$tmp" "$DEST/$s/SKILL.md"
    echo "  ✓ $s"
  else
    rm -f "$tmp"
    echo "  ✗ $s — download failed or payload was not the skill file ($SKILLS_BASE/$s/SKILL.md)" >&2
    exit 1
  fi
done

# #2329/#2330 (codex): Codex reads repo-root AGENTS.md as its standing
# instructions (it auto-loads per session; there is NO skills-declaration
# syntax in AGENTS.md). Emit a Tortoise block so every session knows when and
# how to use the graph — idempotent (marker-delimited replace).
emit_codex_agents_block() {
  local file="AGENTS.md"
  local ms="<!-- tortoise:start -->"
  local me="<!-- tortoise:end -->"
  local tmpdir block pre post
  tmpdir="$(mktemp -d)" || return 1
  block="$tmpdir/block"; pre="$tmpdir/pre"; post="$tmpdir/post"
  printf '%s\n' \
"${ms}
# Tortoise — agent memory (this project is connected to Tortoise Cloud)
- Tortoise is an epistemic memory graph. You have its MCP tools (tortoise_search,
  tortoise_query, tortoise_health, plus graph-write tools) AND these skills
  (how-to-use-tortoise, tortoise-decide, tortoise-file-finding, tortoise-onboarding).
- When the user says \\"Set up Tortoise\\", run the tortoise-onboarding flow and verify
  with tortoise_health before reporting done.
- Use the graph without being asked to: after a decision or a research finding, file
  it (tortoise-file-finding / tortoise-decide handle the mechanics). Search the graph
  before answering from memory; say plainly when nothing relevant is stored.
- First-time MCP calls may prompt for approval — tortoise_health and the read tools
  are safe to allow.
${me}" > "$block"
  if [ -f "$file" ]; then
    if grep -qF "$ms" "$file" && grep -qF "$me" "$file"; then
      # In-place replace between the markers (idempotent refresh) — never
      # touches content outside the marked block.
      : > "$pre"; : > "$post"
      awk -v ms="$ms" -v me="$me" -v pre="$pre" -v post="$post" '
        BEGIN { inmarker = 0; ended = 0 }
        index($0, ms) { inmarker = 1; next }
        inmarker && index($0, me) { inmarker = 0; ended = 1; next }
        { if (ended) print > post; else if (!inmarker) print > pre }
      ' "$file"
      cat "$pre" "$block" "$post" > "$file.tmp" && mv "$file.tmp" "$file"
    else
      printf '\n\n' >> "$file"
      cat "$block" >> "$file"
    fi
  else
    cat "$block" > "$file"
  fi
  rm -rf "$tmpdir"
  echo "  ✓ AGENTS.md — Tortoise standing instructions refreshed/created"
}

# Verify the target dir — we KNOW where we wrote, so this is a local check.
missing=()
for s in "${SKILLS[@]}"; do
  [ -f "$DEST/$s/SKILL.md" ] || missing+=("$s")
done

if [ ${#missing[@]} -eq 0 ]; then
  echo ""
  echo "✅ Tortoise skills installed to $DEST"
  echo "   ${SKILLS[*]}"
  echo ""
  echo "Next: restart your agent, then confirm the skills are listed:"
  case "$HARNESS" in
    claude) echo "   claude — the skills appear under /skills" ;;
    codex)  echo "   codex — open the project in Codex and check /skills, or ask the agent \"Set up Tortoise\"" ;;
    cursor) echo "   cursor — skills load from .cursor/skills" ;;
    pi)     echo "   pi — ~/.pi/agent/skills is scanned on startup" ;;
  esac

  # #2329/#2330: Codex standing instructions — only for the codex harness
  # (other harnesses have their own memory mechanisms; a stray AGENTS.md
  # would change every agent's behavior in this project).
  if [ "$HARNESS" = "codex" ]; then
    emit_codex_agents_block
  fi
else
  echo ""
  echo "⚠️  Some skills did not verify in $DEST: ${missing[*]}" >&2
  echo "   Check the directory + permissions, then re-run the installer." >&2
  exit 1
fi
