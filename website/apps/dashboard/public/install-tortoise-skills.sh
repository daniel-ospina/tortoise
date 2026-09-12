#!/usr/bin/env bash
# install-tortoise-skills.sh — install the official Tortoise agent skills.
#
# The skills are downloaded from the Tortoise product site
# (https://app.premiselabs.co/skills/<name>/SKILL.md) — no git clone, no
# third-party repo. The canonical skill sources (and this script) live in the
# public repo https://github.com/daniel-ospina/tortoise — script at
# website/apps/dashboard/public/install-tortoise-skills.sh, skills under
# skills/ and tortoise/onboarding/.
#
# Project-scoped for Claude Code / Codex / Cursor (installs into the current
# project's skills dir — version-controllable, non-destructive to the
# machine); personal for Pi (~/.pi/agent/skills — the only supported path).
#
# Usage:
#   curl -fsSL https://app.premiselabs.co/install-tortoise-skills.sh | bash -s -- --harness claude
#   (or codex | cursor | pi)
#
# Idempotent: re-running updates the skills in place and refreshes the version
# stamp. Prints the verify step.
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
                                       (Codex also scans ~/.agents/skills — this
                                       installer writes the project root only)
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

# Version stamp (#3): record the installed version in a SIDECAR, never in the
# skill bodies — the served SKILL.md files must stay byte-identical to their
# originals (the `^name:` payload check below and the canonical<->mirror parity
# test both depend on that). Read any previous stamp first so a re-run can
# report an upgrade and flag an on-disk copy that drifted.
STAMP="$DEST/.tortoise-skills-version"
prev_version=""
if [ -f "$STAMP" ]; then
  # `awk ... exit` (not `sed | head`) — reading an advisory file must never
  # abort the install under `set -euo pipefail`, and an unreadable stamp is
  # not fatal.
  prev_version="$(awk -F= '/^skills_version=/{print $2; exit}' "$STAMP" 2>/dev/null || true)"
fi

# Cross-platform digest (macOS ships shasum, GNU userland ships sha256sum).
# Empty when neither exists — the stamp then records the version only, rather
# than advertising content identity it cannot compute.
if command -v sha256sum >/dev/null 2>&1; then SHA_TOOL="sha256sum"
elif command -v shasum >/dev/null 2>&1; then SHA_TOOL="shasum"
else SHA_TOOL=""; fi

sha256_of() {
  case "$SHA_TOOL" in
    sha256sum) sha256sum "$1" | cut -d' ' -f1 ;;
    shasum)    shasum -a 256 "$1" | cut -d' ' -f1 ;;
    *)         return 1 ;;
  esac
}

for s in "${SKILLS[@]}"; do
  mkdir -p "$DEST/$s"
  # mktemp (unpredictable name) so a planted `SKILL.md.tmp` symlink/hardlink in
  # an untrusted project clone cannot make curl clobber an arbitrary file.
  tmp="$(mktemp "$DEST/$s/SKILL.md.XXXXXX" 2>/dev/null || true)"
  if [ -n "$tmp" ] && curl -fsSL --max-time 20 "$SKILLS_BASE/$s/SKILL.md" -o "$tmp" \
      && [ ! -L "$tmp" ] && grep -q "^name: $s$" "$tmp"; then
    # Drift detection (#3): compare the on-disk copy against the digest the
    # last install recorded — content, not version, so a local edit is caught
    # even across a version bump. With no recorded digest (an older or manual
    # install) fall back to comparing against the incoming file.
    if [ -f "$DEST/$s/SKILL.md" ]; then
      cmp_rc=0
      cmp -s "$DEST/$s/SKILL.md" "$tmp" 2>/dev/null || cmp_rc=$?
      if [ "$cmp_rc" -eq 0 ]; then
        : # already identical to the incoming file — nothing can be lost
      else
        recorded=""
        if [ -f "$STAMP" ]; then
          recorded="$(awk -F= -v k="sha256.$s" '$1 == k {print $2; exit}' "$STAMP" 2>/dev/null || true)"
        fi
        disk_hash="$(sha256_of "$DEST/$s/SKILL.md" 2>/dev/null || true)"
        if [ -n "$recorded" ] && [ -n "$disk_hash" ] && [ "$disk_hash" != "$recorded" ]; then
          echo "  ⚠ $s — installed copy was edited locally; overwriting" >&2
        elif [ -z "$recorded" ]; then
          echo "  ⚠ $s — replacing a differing on-disk copy from an older/manual install" >&2
        fi
      fi
    fi
    # mktemp creates 0600; keep the payload readable like the pre-mktemp
    # `curl -o` temp was (same reason the stamp temp gets chmod 0644 below).
    chmod 0644 "$tmp" 2>/dev/null || true
    mv "$tmp" "$DEST/$s/SKILL.md"
    echo "  ✓ $s"
  else
    [ -n "$tmp" ] && rm -f "$tmp"
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
  local tmpdir block pre post target
  tmpdir="$(mktemp -d)" || return 1
  block="$tmpdir/block"; pre="$tmpdir/pre"; post="$tmpdir/post"
  # Resolve a symlinked AGENTS.md (monorepos point AGENTS.md at a shared
  # file) — refresh the TARGET, never clobber the link.
  target="$file"
  [ -L "$file" ] && target="$(readlink "$file")"
  printf '%s\n' \
"${ms}
# Tortoise — agent memory (this project is connected to Tortoise Cloud)
- If the Tortoise MCP tools are available in this session (search/query/health +
  graph-write tools) and these skills (how-to-use-tortoise, tortoise-decide,
  tortoise-file-finding, tortoise-onboarding) are installed, use the graph without
  being asked to: after a decision or a research finding, file it; search the graph
  before answering from memory; say plainly when nothing relevant is stored.
- When the user says \"Set up Tortoise\", run the tortoise-onboarding flow and verify
  with tortoise_health before reporting done.
- First-time MCP calls may prompt for approval — tortoise_health and the read tools
  are safe to allow.
${me}" > "$block"
  if [ -f "$target" ]; then
    if grep -qF "$ms" "$target" && grep -qF "$me" "$target"; then
      # In-place replace between the markers (idempotent refresh) — never
      # touches content outside the marked block. Markers must sit on their
      # own line (tolerating a trailing CR) so content merely mentioning the
      # marker text is preserved.
      : > "$pre"; : > "$post"
      awk -v ms="$ms" -v me="$me" -v pre="$pre" -v post="$post" '
        BEGIN { inmarker = 0; ended = 0 }
        $0 ~ ("^" ms "\r?$") { inmarker = 1; next }
        inmarker && $0 ~ ("^" me "\r?$") { inmarker = 0; ended = 1; next }
        { if (ended) print > post; else if (!inmarker) print > pre }
      ' "$target"
      # cp -p first preserves the file's mode (a 0600 AGENTS.md stays 0600)
      # and ownership; cat then overwrites content in place.
      cp -p "$target" "$target.tmp"
      cat "$pre" "$block" "$post" > "$target.tmp"
      mv "$target.tmp" "$target"
    else
      printf '\n\n' >> "$target"
      cat "$block" >> "$target"
    fi
  else
    cat "$block" > "$target"
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
  # Version stamp (#3): sidecar manifest, written only after every skill
  # verified — a failed install never claims a version it did not place.
  # Written to a mktemp-created temp file then `mv`d into place: `mv` REPLACES
  # a symlink instead of writing through it, the write is atomic (an
  # interrupted run cannot leave a truncated stamp), and the unpredictable
  # mktemp name also defeats a pre-planted `$STAMP.tmp` symlink/hardlink.
  stamp_tmp=""
  stamp_ok=0
  if [ ! -d "$STAMP" ]; then
    stamp_tmp="$(mktemp "${STAMP}.XXXXXX" 2>/dev/null || true)"
  fi
  if [ -n "$stamp_tmp" ] && {
    printf '%s\n' \
      "# Tortoise skills install manifest — written by install-tortoise-skills.sh." \
      "# Do not edit by hand; re-run the installer to refresh this stamp." \
      "skills_version=$SKILLS_VERSION" \
      "harness=$HARNESS" \
      "source=$SKILLS_BASE" \
      "skills=${SKILLS[*]}"
    if [ -n "$SHA_TOOL" ]; then
      for s in "${SKILLS[@]}"; do
        printf 'sha256.%s=%s\n' "$s" "$(sha256_of "$DEST/$s/SKILL.md")"
      done
    fi
  } > "$stamp_tmp" 2>/dev/null; then
    # mktemp creates 0600; keep the stamp world-readable like a normal
    # redirection would, so a shared/CI checkout can still read it.
    chmod 0644 "$stamp_tmp" 2>/dev/null || true
    if mv -f "$stamp_tmp" "$STAMP" 2>/dev/null \
        && [ -f "$STAMP" ] && [ ! -L "$STAMP" ]; then
      stamp_tmp=""
      stamp_ok=1
    fi
  fi
  [ -n "$stamp_tmp" ] && rm -f "$stamp_tmp" || true
  echo ""
  echo "✅ Tortoise skills installed to $DEST"
  echo "   ${SKILLS[*]} (${SKILLS_VERSION})"
  if [ "$stamp_ok" -eq 1 ]; then
    if [ -n "$prev_version" ] && [ "$prev_version" != "$SKILLS_VERSION" ]; then
      echo "   ⬆  version stamp updated: $prev_version -> $SKILLS_VERSION"
    fi
    echo "   version stamp: $STAMP (cat it to see what is installed)"
  else
    echo "   ⚠ could not write the version stamp to $STAMP" >&2
  fi
  echo ""
  echo "Next: restart your agent, then confirm the skills are listed:"
  case "$HARNESS" in
    claude) echo "   claude — the skills appear under /skills" ;;
    codex)  echo "   codex — open the project in Codex and check /skills, or ask the agent \"Set up Tortoise\"" ;;
    cursor) echo "   cursor — skills load from .cursor/skills" ;;
    pi)
      echo "   pi — skills load from ~/.pi/agent/skills when the session starts."
      echo "   pi — verify the MCP CONNECTION too (installed skills are not proof it connected):"
      echo "        ask the agent to call tortoise_health — \"ok\" is a graph-status reply, not a tool error."
      echo "        If tortoise_health is missing, the first connect to the cold hosted machine"
      echo "        failed: have the agent run  mcp_load tortoise  and call tortoise_health again."
      ;;
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
