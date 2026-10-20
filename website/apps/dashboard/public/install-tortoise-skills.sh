#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Premise Labs
#
# ── Licence: MIT (in-band, not a served sidecar) ───────────────────────────
# This installer is part of the Tortoise CONSUMER surface and is licensed under
# MIT — it does NOT inherit the repository's BSL, which governs the engine.
#
# The notice is reproduced INSIDE this file on purpose. A `curl … | bash` user
# receives this script's bytes and nothing else: a licence file served next to
# the script (or next to the skills) never travels with a piped download, so
# MIT's "included in all copies" condition can only be met for a single-file
# script by carrying the notice in the file itself. (Contrast #526's client
# dist, where client/LICENSE ships INSIDE the wheel — the notice is packaged
# with the artifact.) The same MIT text is served beside the skills at
# https://app.premiselabs.co/skills/LICENSE.
#
# MIT License
#
# Copyright (c) 2026 Premise Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# ───────────────────────────────────────────────────────────────────────────
# install-tortoise-skills.sh — install the official Tortoise agent skills.
#
# The skills are downloaded from the Tortoise product site
# (https://app.premiselabs.co/skills/<name>/SKILL.md) — no git clone, no
# third-party repo.
#
# ── WHICH REPO AUTHORS WHAT (#4643) ────────────────────────────────────────
# Two repos hold Tortoise consumer artifacts, and editing the wrong one is a
# drift rather than a fork. This header is the statement a contributor reads:
#
#   * The three reusable skills this script installs are AUTHORED in the public
#     MIT repo https://github.com/daniel-ospina/tortoise-skills-and-integrations.
#     The copies served here are a DERIVED MIRROR: they are pinned in
#     `config/served-skills-upstream.yml` and gated fail-closed by
#     `tools/served_skills_parity.py`: the offline served↔pin check runs per-PR,
#     and the upstream (honesty/currency/scope) arm runs before the dashboard
#     deploy and daily on a schedule. `--sync` re-derives. To change one of those
#     skills, edit it UPSTREAM and re-derive — never edit a served copy here.
#   * THIS script and the onboarding instructions are authored HERE, in
#     https://github.com/daniel-ospina/tortoise — the script at
#     `website/apps/dashboard/public/install-tortoise-skills.sh` (its source of
#     truth was moved out of the MIT repo by #3324 because that copy went
#     stale) and the onboarding document at `tortoise/onboarding/SKILL.md`
#     (served, but not installed by this script since #4365).
#
# The ownership rationale and the artifacts NOT covered by the pin:
# `docs/license-notes.md` §7.
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
# stamp. A destination that already holds a file we did not write is backed up
# once and then MERGED — our payload becomes the file, foreign frontmatter keys
# and the foreign banner/trailer blocks are preserved, never silently dropped
# (#4327). Prints the verify step.
set -euo pipefail

SKILLS_VERSION="v3"   # bump when the skill set changes
SKILLS_BASE="https://app.premiselabs.co/skills"
# v3 (#4365): -tortoise-onboarding. Onboarding is a one-time SETUP FLOW, not a
# reusable capability, so it is NOT installed into a harness's skills
# namespace — it is DELIVERED as instructions (the served document at
# $SKILLS_BASE/tortoise-onboarding/SKILL.md, also printed by `tortoise init`)
# plus the per-harness MCP config the dashboard's universal command writes.
# This installer ships the three reusable capabilities only. v2 (#1998 W2) had
# added the 4th; it is also the one basename with no upstream counterpart, so
# it widened a flat, shared namespace this installer only MERGES into (#4327).
SKILLS=(how-to-use-tortoise tortoise-decide tortoise-file-finding)

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

# ── Foreign-content preservation (#4327) ──────────────────────────────────
# $DEST is a SHARED, flat namespace. On the measured machine a different
# owner's tool (agent-infra) had already written three of these basenames,
# and the 2026-09-20 install replaced them verbatim: no merge, no backup, no
# message, silently stripping two machine-wide conventions carried by 95-96
# of 122 installed skills — a `subjects.team:` frontmatter key and the
# `⛔ … MUST be read in full — not skimmed.` banner + its trailing line.
#
# Chosen behaviour (research + precedent in the PR body): a `curl | bash`
# install must stay non-interactive, so a hard refusal is out; a preserving
# write is the safe default. Concretely — BACK UP a pre-existing copy once,
# then MERGE: the payload becomes the file, but every foreign frontmatter KEY
# (one the payload does not define) and every foreign seam BLOCK (the leading
# blockquote run after the frontmatter, and a trailing blockquote run after a
# `---` rule) is kept. The seams are positional on purpose: they are the only
# places an external tool can annotate a foreign file without its text being
# read as the skill's own instructions, so preserving them cannot union two
# whole documents. The positional rule is deliberately CONSERVATIVE: a banner
# is kept only when it is the blockquote run that OPENS the body (one placed
# after a `# Title` is not detected), and a trailer only when it is a
# blockquote run directly after a `---` rule (a plain-paragraph trailer is not
# detected). Widening either seam would start reading ordinary body content as
# a foreign annotation; the `.bak` still holds anything the rule misses.
#
# The merge is ADDITIVE-PRESERVING: it can retain content that is merely
# stale (the `.bak` and the printed report make that visible), whereas
# replacing deletes unrecoverable foreign content. That is the correct
# direction for a shared namespace.
#
# merge_skill_file <incoming-payload> <existing-file> <report-file>
#   → merged content on stdout; one `preserved-*` line per kept item in the
#     report file.
merge_skill_file() {
  awk -v rpt="$3" '
    function keyof(s) {
      if (s ~ /^[A-Za-z0-9_.-]+:/) return substr(s, 1, index(s, ":") - 1)
      return ""
    }
    FNR == 1 { f++ }
    { if (f == 1) p[++pn] = $0; else e[++en] = $0 }
    END {
      printf "" > rpt

      # frontmatter bounds: opening `---` … next `---`
      if (pn >= 1 && p[1] ~ /^---[ \t]*$/)
        for (i = 2; i <= pn; i++) if (p[i] ~ /^---[ \t]*$/) { p_end = i; break }
      if (en >= 1 && e[1] ~ /^---[ \t]*$/)
        for (i = 2; i <= en; i++) if (e[i] ~ /^---[ \t]*$/) { e_end = i; break }

      # payload + existing frontmatter entries (a key owns its indented
      # continuation lines, so block lists survive intact)
      for (i = 2; i < p_end; i++) {
        k = keyof(p[i])
        if (k != "") { pfn++; pk[pfn] = k; ptxt[pfn] = p[i]; pset[k] = 1 }
        else if (pfn > 0) ptxt[pfn] = ptxt[pfn] "\n" p[i]
      }
      for (i = 2; i < e_end; i++) {
        k = keyof(e[i])
        if (k != "") { efn++; ek[efn] = k; etxt[efn] = e[i] }
        else if (efn > 0) etxt[efn] = etxt[efn] "\n" e[i]
      }

      # every payload body line — the "is this already ours?" test
      pbs = p_end + 1
      for (i = pbs; i <= pn; i++) pbody[p[i]] = 1
      ebs = e_end + 1

      # ── frontmatter: payload keys, then foreign keys appended ─────
      if (p_end > 0) {
        print p[1]
        for (i = 1; i <= pfn; i++) print ptxt[i]
        for (i = 1; i <= efn; i++)
          if (!(ek[i] in pset)) {
            print etxt[i]
            printf "preserved-frontmatter: %s\n", ek[i] > rpt
          }
        print p[p_end]
      }

      # ── foreign banner: leading blockquote run of the existing body ──
      j = ebs
      while (j <= en && e[j] ~ /^[ \t]*$/) j++
      runn = 0
      while (j <= en && (e[j] ~ /^[ \t]*>/ || e[j] ~ /^[ \t]*$/)) {
        runn++; run[runn] = e[j]; j++
      }
      # keep the blockquote lines not already ours, plus the blank lines that
      # separated them (a two-paragraph banner stays two paragraphs)
      lastkept = 0
      for (i = 1; i <= runn; i++)
        if (run[i] ~ /^[ \t]*>/ && !(run[i] in pbody)) {
          if (lastkept > 0) {
            gap = 0
            for (jj = lastkept + 1; jj < i; jj++)
              if (run[jj] ~ /^[ \t]*$/) gap = 1
            if (gap) { fpre++; fpreline[fpre] = "" }
          }
          fpre++; fpreline[fpre] = run[i]
          lastkept = i
        }
      if (fpre > 0) {
        for (i = 1; i <= fpre; i++) print fpreline[i]
        print ""
        printf "preserved-banner: %d line(s)\n", fpre > rpt
        # the banner now occupies the payload own leading blank, so do not
        # emit it twice (keeps the merged shape identical across re-runs)
        while (pbs <= pn && p[pbs] ~ /^[ \t]*$/) pbs++
      }

      # ── payload body ─────────────────────────────────────────────
      for (i = pbs; i <= pn; i++) print p[i]

      # ── foreign trailer: trailing blockquote run after a `---` rule ──
      k = en
      while (k >= ebs && e[k] ~ /^[ \t]*$/) k--
      if (k >= ebs && e[k] ~ /^[ \t]*>/) {
        r2 = 0
        while (k >= ebs && e[k] ~ /^[ \t]*>/) { r2++; tmp[r2] = e[k]; k-- }
        kk = k
        while (kk >= ebs && e[kk] ~ /^[ \t]*$/) kk--
        if (kk >= ebs && e[kk] ~ /^---[ \t]*$/)
          for (i = r2; i >= 1; i--)
            if (!(tmp[i] in pbody)) {
              fepi++; fepiline[fepi] = tmp[i]
            }
      }
      if (fepi > 0) {
        print "---"
        for (i = 1; i <= fepi; i++) print fepiline[i]
        printf "preserved-trailer: %d line(s)\n", fepi > rpt
      }
    }
  ' "$1" "$2"
}

for s in "${SKILLS[@]}"; do
  mkdir -p "$DEST/$s"
  # mktemp (unpredictable name) so a planted `SKILL.md.tmp` symlink/hardlink in
  # an untrusted project clone cannot make curl clobber an arbitrary file.
  tmp="$(mktemp "$DEST/$s/SKILL.md.XXXXXX" 2>/dev/null || true)"
  if [ -n "$tmp" ] && curl -fsSL --max-time 20 "$SKILLS_BASE/$s/SKILL.md" -o "$tmp" \
      && [ ! -L "$tmp" ] && grep -q "^name: $s$" "$tmp"; then
    # mktemp creates 0600; keep the payload readable like the pre-mktemp
    # `curl -o` temp was (same reason the stamp temp gets chmod 0644 below).
    # Hoisted above BOTH write paths below: a fresh destination is `mv`d from
    # this file, so a first install would otherwise land 0600.
    chmod 0644 "$tmp" 2>/dev/null || true
    dest="$DEST/$s/SKILL.md"
    if [ ! -f "$dest" ]; then
      mv "$tmp" "$dest"
      echo "  ✓ $s"
      continue
    fi

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
          # `merging over it`, not `overwriting`: the write path below MERGES
          # preserving foreign content and keeps a first-backup-wins `.bak`.
          echo "  ⚠ $s — installed copy was edited locally; merging over it" >&2
        elif [ -z "$recorded" ]; then
          echo "  ⚠ $s — merging over a differing on-disk copy from an older/manual install" >&2
        fi
      fi
    fi

    # Destination exists. Merge our payload with whatever is there, keeping
    # foreign content (above), and back the pre-existing copy up ONCE —
    # first-backup-wins leaves the pre-Tortoise revision recoverable across
    # re-runs instead of letting a re-run overwrite the only copy of it.
    merged="$DEST/$s/SKILL.md.merged.$$"
    report="$DEST/$s/SKILL.md.preserved.$$"
    if ! merge_skill_file "$tmp" "$dest" "$report" > "$merged"; then
      rm -f "$tmp" "$merged" "$report"
      echo "  ✗ $s — could not merge into the existing $dest" >&2
      exit 1
    fi

    if cmp -s "$dest" "$merged"; then
      # Already current — our content plus the preserved foreign content.
      rm -f "$tmp" "$merged" "$report"
      echo "  ✓ $s (already current)"
      continue
    fi

    backed_up=""
    if [ ! -e "$dest.bak" ]; then
      if ! cp -p "$dest" "$dest.bak"; then
        rm -f "$tmp" "$merged" "$report"
        echo "  ✗ $s — could not back up $dest to $dest.bak" >&2
        exit 1
      fi
      backed_up="$dest.bak"
    fi
    if ! mv "$merged" "$dest"; then
      rm -f "$tmp" "$merged" "$report"
      echo "  ✗ $s — could not write the merged skill to $dest" >&2
      exit 1
    fi
    rm -f "$tmp"
    echo "  ✓ $s (merged)"
    if [ -n "$backed_up" ]; then
      echo "      · backed up existing copy → $backed_up"
    else
      echo "      · existing backup kept → $dest.bak"
    fi
    # Report the foreign content explicitly surfaced by the merge (the same
    # logic that preserved it), so a destructive-looking install is visibly
    # not destructive.
    if [ -s "$report" ]; then
      while IFS= read -r line; do echo "      · $line"; done < "$report"
    fi
    rm -f "$report"
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
#
# #3429: the block is VERSIONED. The markers make a re-run idempotent, but
# nothing made a STALE block DETECTABLE — and a repo provisioned before the
# text changed keeps re-reading the old block forever, because the file is
# someone else's and the installer is the only thing that rewrites it. The
# version line is inside the marker window, so the replace below refreshes it
# too. BUMP `AGENTS_BLOCK_VERSION` whenever the block's text changes; the
# upgrade path needs nothing else.
AGENTS_BLOCK_VERSION="2"

emit_codex_agents_block() {
  local file="AGENTS.md"
  local ms="<!-- tortoise:start -->"
  local me="<!-- tortoise:end -->"
  local tmpdir block pre post target staged
  tmpdir="$(mktemp -d)" || {
    echo "  ✗ AGENTS.md — could not create a scratch directory — nothing was changed" >&2
    echo "  (skills above ARE installed; only the AGENTS.md block was not upgraded — exit 3)" >&2
    exit 3
  }
  block="$tmpdir/block"; pre="$tmpdir/pre"; post="$tmpdir/post"
  # ── PRE-STATE: read BEFORE anything is written (after the write the file
  # always looks current). The decision uses the SAME line-anchored test the
  # replace uses; a near-anchor or mangled marker is MALFORMED, because treating
  # it as absent made the state `none`, which APPENDS — duplicating the block
  # with the stale copy first, and then every later run compared the appended
  # pair against itself and reported "already current".
  #
  # FAIL-CLOSED, EXPLICITLY: this function is called as `emit … || { exit 3; }`,
  # and bash does NOT apply `errexit` to any command inside a function invoked in
  # that context. Every step that can fail is therefore checked by hand — an
  # unchecked `awk`/`cat` failure used to end in a SUCCESS report over an
  # untouched file, which is the harm this whole change exists to end.
  local state="" prev_version="" same_bytes=0
  local state_kind="" c_start c_startline c_end c_endline c_fence_start
  local c_near_s c_near_e c_mangled_s c_mangled_e c_fence_open
  _emit_fail() {
    # ONE place that cleans up and fails. It EXITS rather than returning: a
    # `cmd || helper` list is itself an errexit-exempt context, so a helper that
    # merely returns 1 lets the next statement run — which is how an internal
    # read failure ended in a SUCCESS report over an untouched file.
    echo "  ✗ AGENTS.md — $1" >&2
    echo "  (skills above ARE installed; only the AGENTS.md block was not upgraded — exit 3)" >&2
    rm -rf "$tmpdir"
    exit 3
  }
  local ms_re="^${ms}\r?$" me_re="^${me}\r?$"
  local ms_ws me_ws
  # NB: `tr` failing here silently disables the mangled-marker detector (both
  # whitespace-stripped markers become ""), which reverts the duplicate-block
  # path — so it is checked like everything else, not assumed.
  ms_ws="$(printf '%s' "$ms" | tr -d '[:space:]')" || _emit_fail "could not prepare marker matching — nothing was changed"
  me_ws="$(printf '%s' "$me" | tr -d '[:space:]')" || _emit_fail "could not prepare marker matching — nothing was changed"
  [ -n "$ms_ws" ] && [ -n "$me_ws" ] || _emit_fail "could not prepare marker matching — nothing was changed"
  # Resolve a symlinked AGENTS.md (monorepos point AGENTS.md at a shared
  # file) — refresh the TARGET, never clobber the link.
  target="$file"
  [ -L "$file" ] && target="$(readlink "$file")"

  printf '%s\n' \
"${ms}
<!-- tortoise:block v${AGENTS_BLOCK_VERSION} -->
# Tortoise — agent memory (this project is connected to Tortoise Cloud)
- If the Tortoise MCP tools are available in this session (search/query/health +
  graph-write tools) and these skills (how-to-use-tortoise, tortoise-decide,
  tortoise-file-finding) are installed, use the graph without
  being asked to: after a decision or a research finding, file it; search the graph
  before answering from memory; say plainly when nothing relevant is stored.
- When the user says \"Set up Tortoise\", follow the onboarding instructions at
  $SKILLS_BASE/tortoise-onboarding/SKILL.md. Write the MCP config FIRST, then
  restart the client so the new session reads it — the server is loaded at
  session start, so the rest of THIS session cannot see it. Only in that NEW
  session run the verify with tortoise_health, and report done from there.
  Onboarding is delivered as INSTRUCTIONS, not as an installed skill.
- First-time MCP calls may prompt for approval — tortoise_health and the read tools
  are safe to allow.
${me}" > "$block" || _emit_fail "could not build the new block text — nothing was changed"

  if [ -f "$target" ]; then
    # ONE pass decides WHERE the block is, and every reader/writer below obeys
    # its line numbers. Counting with fences but then locating the window with a
    # fence-blind scan is how the fenced-example feature deleted a user's
    # documentation and then dumped a duplicate block outside any marker: the
    # state said "paired" while the writers latched onto the FIRST anchored
    # markers in the file, which were the example's.
    state="$(LC_ALL=C awk -v ms="$ms" -v me="$me" -v msws="$ms_ws" -v mews="$me_ws" \
                 -v msre="$ms_re" -v mere="$me_re" '
      # NEAR: the marker ALONE on the line once whitespace/BOM is stripped, but
      # not matching the anchored form (indented, trailing space, BOM).
      function near(line, m,   p, pre, post) {
        p = index(line, m)
        if (p == 0) return 0
        pre = substr(line, 1, p - 1); post = substr(line, p + length(m))
        gsub(/[ \t\r]/, "", pre); gsub(/[ \t\r]/, "", post)
        # Strip the UTF-8 BOM as BYTES: under a UTF-8 locale `\357` is the
        # CHARACTER U+00EF, so the escape-based strip silently did nothing and the
        # documented "BOM-prefixed" cause could never print.
        if (substr(pre, 1, 3) == sprintf("%c%c%c", 239, 187, 191)) pre = substr(pre, 4)
        return (pre == "" && post == "")
      }
      # MANGLED: a marker with different INTERNAL spacing and nothing else on the
      # line — a broken marker, not prose about one.
      function stripped(line,   x) { x = line; gsub(/[ \t\r]/, "", x); return x }
      function classify(line, nr,   sw) {
        if (line ~ msre) { starts++; if (!startline) startline = nr; return }
        if (line ~ mere) { ends++;   if (!endline)   endline   = nr; return }
        if (near(line, ms)) { nearstart++; return }
        if (near(line, me)) { nearend++;   return }
        sw = stripped(line)
        if (sw != "" && sw == msws) mangledstart++
        else if (sw != "" && sw == mews) mangledend++
      }
      BEGIN { starts = ends = nearstart = nearend = mangledstart = mangledend = 0; fence = ""; flen = 0 }
      # The FENCE RUN on this line, or "": CommonMark opens a fence with three or
      # more backticks OR tildes (indented at most 3 spaces) and closes it only
      # with a run of the SAME character at least as long. A parity toggle on
      # "three backticks" is not this: `~~~` was not a fence at all (so an example
      # in one was DELETED and reported as an upgrade), and a 4-backtick fence
      # around a 3-backtick example closed at the wrong line.
      function fence_run(line,   m) {
        if (!match(line, /^ {0,3}(`{3,}|~{3,})/)) return ""
        m = substr(line, RSTART, RLENGTH)
        sub(/^ +/, "", m)
        return m
      }
      # A CLOSING fence carries nothing after the run but spaces/tabs. Trailing
      # text is legal only on the OPENING fence, so a language-tagged line INSIDE a
      # fence is content — treating it as a close ended the fence early, counted the
      # rest of the example, and produced a false "ran twice" whose remedy deletes
      # the real block plus the documentation.
      function fence_close(line,   m) {
        if (!match(line, /^ {0,3}(`{3,}|~{3,})[ \t\r]*$/)) return ""
        m = substr(line, RSTART, RLENGTH)
        gsub(/[ \t\r]/, "", m)
        return m
      }
      { lines[NR] = $0 }
      { fr = fence_run($0)
        if (fence == "") {
          if (fr != "") { fence = substr(fr, 1, 1); flen = length(fr); fstart = NR; next }
        } else {
          # Inside a fence nothing is content — including a shorter run of the same
          # character and a run of the OTHER character (a nested example).
          if (fr != "" && fence_close($0) != "" && substr(fr, 1, 1) == fence && length(fr) >= flen) {
            for (i = fstart; i < NR; i++) closed[i] = 1
            fence = ""
          }
          next
        } }
      { classify($0, NR) }
      END {
        # An UNCLOSED fence would hide every real marker below it, and "no
        # markers" means APPEND — one more copy per run. Fall back to the
        # fence-blind pass (the pre-#3429 behaviour, which replaced the real
        # block correctly) instead of treating an unterminated fence as content.
        # Recount ONLY the lines the fence pass skipped and never revisited: those
        # inside the fence that is still open. A properly CLOSED fence stays
        # skipped — recounting the whole file treated a documented example as the
        # block, replacing it (measured), or hard-refused a file with no duplicate
        # at all while advising the user to delete their documentation.
        fenceopen = 0
        if (fence != "") {
          fenceopen = 1
          starts = ends = nearstart = nearend = mangledstart = mangledend = 0
          startline = endline = 0
          for (i = 1; i <= NR; i++) if (!closed[i]) classify(lines[i], i)
        }
        # ONE clean pair wins even if OTHER lines carry near/mangled markers: they
        # are outside its window, so the split preserves them verbatim. Refusing
        # them treated a 4-space-indented documentation example (CommonMark allows
        # that as a code block too) as a "duplicate" and its remedy deleted it.
        if (starts == 1 && ends == 1 && startline < endline) {
          printf "paired|1|%d|1|%d|0|0|0|0|%d|%d\n", startline, endline, fenceopen, (fence != "" ? fstart : 0); exit
        }
        if (starts + ends + nearstart + nearend + mangledstart + mangledend == 0) {
          printf "none|0|-|0|-|0|0|0|0|%d|%d\n", fenceopen, (fence != "" ? fstart : 0); exit
        }
        # Per-marker counts, so the message and the remedy agree with each other:
        # `c_end` (anchored only) used to say "there is no end line" while the
        # line it was printed under reported a mangled one.
        printf "malformed|%d|%s|%d|%s|%d|%d|%d|%d|%d|%d\n", starts, (startline ? startline : "-"), ends, (endline ? endline : "-"), nearstart, nearend, mangledstart, mangledend, fenceopen, (fence != "" ? fstart : 0)
      }' "$target")" || _emit_fail "could not read $target (unreadable, or not a regular file) — nothing was changed"
    IFS='|' read -r state_kind c_start c_startline c_end c_endline \
      c_near_s c_near_e c_mangled_s c_mangled_e c_fence_open c_fence_start <<EOF
$state
EOF
  else
    state_kind="none"
    c_fence_open=0
    c_fence_start=0
  fi

  case "$state_kind" in
    paired|none) : ;;
    malformed)
      local s_side=$(( ${c_start:-0} + ${c_near_s:-0} + ${c_mangled_s:-0} ))
      local e_side=$(( ${c_end:-0} + ${c_near_e:-0} + ${c_mangled_e:-0} ))
      echo "  ✗ AGENTS.md — the Tortoise block markers are MALFORMED" >&2
      echo "      start markers: ${c_start:-0} (first at line ${c_startline:--}), end markers: ${c_end:-0} (first at line ${c_endline:--})" >&2
      [ $(( ${c_near_s:-0} + ${c_near_e:-0} )) -gt 0 ] && \
        echo "      ${c_near_s:-0} start / ${c_near_e:-0} end marker line(s) are the marker ALONE but indented, trailing-spaced, or BOM-prefixed." >&2
      [ $(( ${c_mangled_s:-0} + ${c_mangled_e:-0} )) -gt 0 ] && \
        echo "      ${c_mangled_s:-0} start / ${c_mangled_e:-0} end line(s) carry a marker that is not exactly the expected text (internal spacing, or an invisible character)." >&2
      echo "      expected exactly ONE ${ms} before exactly ONE ${me}, each alone on its line." >&2
      echo "      NOT rewritten: appending would leave the stale block in place, and a replace" >&2
      echo "      would have to guess which copy is yours." >&2
      if [ "${c_fence_open:-0}" = "1" ]; then
        echo "      An UNTERMINATED code fence is hiding part of this file, so the markers" >&2
        echo "      cannot be told apart from an example. Close the fence (or delete the" >&2
        echo "      fence line) and re-run; nothing was written." >&2
        rm -rf "$tmpdir"
        echo "  (skills above ARE installed; only the AGENTS.md block was not upgraded — exit 3)" >&2
        exit 3
      elif [ "$s_side" -ge 2 ] && [ "$e_side" -ge 1 ]; then
        echo "      Usual cause: an older installer ran twice. Delete from the FIRST start marker" >&2
        echo "      through the LAST end marker (all copies), then re-run." >&2
      elif [ "$s_side" -ge 1 ] && [ "$e_side" -eq 0 ]; then
        echo "      No end marker was found (in any form). Add the ${me} line after the block," >&2
        echo "      or delete the start marker, then re-run." >&2
      elif [ "$e_side" -ge 1 ] && [ "$s_side" -eq 0 ]; then
        echo "      No start marker was found (in any form). Add the ${ms} line before the block," >&2
        echo "      or delete the end marker, then re-run." >&2
      else
        echo "      Fix the marker lines so exactly one clean pair remains, then re-run." >&2
      fi
      rm -rf "$tmpdir"
      echo "  (skills above ARE installed; only the AGENTS.md block was not upgraded — exit 3)" >&2
      exit 3
      ;;
    *)
      _emit_fail "could not determine the block state (installer bug) — nothing was changed"
      ;;
  esac


  # A block written INSIDE an unterminated fence renders as code and is never read
  # by the agent it was written for. The write is what the pre-#3429 installer did,
  # so it is not refused — but it must not be silent.
  block_inside_fence=0
  if [ "${c_fence_open:-0}" = "1" ]; then
    case "$state_kind" in
      # An append lands at EOF, which is after the fence opened.
      none) block_inside_fence=1 ;;
      # A clean pair is REPLACED in place: it is inside the open fence only if the
      # block starts BELOW the fence. A pair ABOVE it is rewritten where it sits
      # and stays readable — warning there was a false alarm (measured), and NOT
      # warning when it IS below was a missed one (measured): both directions are
      # pinned by tests.
      paired) [ "${c_startline:-0}" -gt "${c_fence_start:-0}" ] && block_inside_fence=1 ;;
    esac
  fi
  if [ "$block_inside_fence" = "1" ]; then
    echo "  ⚠ AGENTS.md has an UNTERMINATED code fence — the Tortoise block will be written" >&2
    echo "    inside it and read as code, not as instructions. Close the fence and re-run." >&2
  fi

  if [ "$state_kind" = "paired" ]; then
    # The version is read INSIDE the window, and any token is captured (not just
    # digits) so a mangled version is reported as mangled instead of as
    # "unversioned". The byte comparison decides "already current": a version
    # line is evidence of ORDER, never proof that the text matches.
    prev_version="$(awk -v s="$c_startline" -v e="$c_endline" '
      NR >= s && NR <= e && match($0, /tortoise:block v[^ ]* -->/) {
        v = substr($0, RSTART, RLENGTH)
        sub(/^tortoise:block v/, "", v); sub(/ -->$/, "", v); print v; exit
      }' "$target")" || _emit_fail "could not read the block's version — nothing was changed"

    awk -v s="$c_startline" -v e="$c_endline" 'NR >= s && NR <= e' "$target" > "$tmpdir/window" \
      || _emit_fail "could not extract the existing block — nothing was changed"
    # Line-ending-insensitive: a file whose markers match and whose only
    # difference is CRLF is not a TEXT change (and rewriting it while saying
    # "updated text" would be a false report). Only a CR immediately before the
    # newline is ignored — a CR inside a line is real content, so it still counts.
    #
    # Normalise into REAL FILES and check each step: a failing process
    # substitution yields two EMPTY streams, `cmp` then reports them EQUAL, and
    # the block would be left stale while reporting "already current" — the one
    # place where an error read as the strongest success claim in the function.
    if cmp -s "$tmpdir/window" "$block"; then
      same_bytes=1
    else
      sed "s/\r\$//" "$tmpdir/window" > "$tmpdir/window.lf" \
        || _emit_fail "could not normalise the existing block — nothing was changed"
      sed "s/\r\$//" "$block" > "$tmpdir/block.lf" \
        || _emit_fail "could not normalise the new block — nothing was changed"
      if cmp -s "$tmpdir/window.lf" "$tmpdir/block.lf"; then same_bytes=1; fi
    fi
  fi

  if [ "$state_kind" = "paired" ] && [ "$same_bytes" -eq 0 ]; then
    # In-place replace between the markers — never touches content outside them.
    : > "$pre" || _emit_fail "could not prepare the rewrite — nothing was changed"
    : > "$post" || _emit_fail "could not prepare the rewrite — nothing was changed"
    # The map was computed from an earlier read. In this fleet AGENTS.md is exactly
    # the kind of file another process may write, and a stale line number makes the
    # split delete the wrong lines while every command still succeeds — so the pair
    # is re-asserted here, in the same pass that cuts the file.
    awk -v s="$c_startline" -v e="$c_endline" -v pre="$pre" -v post="$post" \
        -v msre="$ms_re" -v mere="$me_re" '
      NR == s && $0 !~ msre { print "the start marker moved while it was being rewritten" > "/dev/stderr"; exit 9 }
      NR == e && $0 !~ mere { print "the end marker moved while it was being rewritten" > "/dev/stderr"; exit 9 }
      NR < s { print > pre }
      NR > e { print > post }' "$target" \
      || _emit_fail "could not split the file around the block — nothing was changed"
    # Stage through an UNPREDICTABLE name in the SAME directory (so the write is
    # same-filesystem and the `mv` stays atomic). A predictable `$target.tmp` is
    # the #4327 threat model this script already applies to the skill payloads: a
    # planted symlink at that name made `cp` follow it and clobber an arbitrary
    # file, then `mv` turned AGENTS.md into a link to it.
    staged="$(mktemp "$(dirname "$target")/.tortoise-agents.XXXXXX")" \
      || _emit_fail "could not stage a temp file next to $target — nothing was changed"
    # cp -p first preserves the file's mode (a 0600 AGENTS.md stays 0600) and
    # ownership; cat then overwrites content in place.
    cp -p "$target" "$staged" || { rm -f "$staged"; _emit_fail "could not copy $target — nothing was changed"; }
    # The mode is captured so it can be RESTORED, and the staged copy is made
    # writable meanwhile: `cp -p` gives the staged file the target's mode, so a
    # read-only AGENTS.md (0444) could not be upgraded even though the directory
    # and the `mv` were writable — and the failure named no cause at all.
    orig_mode="$(stat -c '%a' "$target" 2>/dev/null || stat -f '%Lp' "$target" 2>/dev/null || true)"
    chmod u+w "$staged" 2>/dev/null || true
    if ! cat "$pre" "$block" "$post" > "$staged"; then
      rm -f "$staged"
      _emit_fail "could not write the staged file — $target is read-only or its directory is not writable; the original is untouched"
    fi
    # Put the user's mode back before the rename, so a read-only AGENTS.md stays
    # read-only instead of being silently loosened to the umask default.
    [ -n "$orig_mode" ] && { chmod "$orig_mode" "$staged" 2>/dev/null || true; }
    mv "$staged" "$target" || { rm -f "$staged"; _emit_fail "could not replace $target — the original is untouched"; }
  elif [ "$state_kind" = "none" ]; then
    if [ -f "$target" ]; then
      printf '\n\n' >> "$target" || _emit_fail "could not append to $target"
      cat "$block" >> "$target" || _emit_fail "could not append the block to $target (partially written — re-run after checking the file)"
    else
      cat "$block" > "$target" || _emit_fail "could not create $target"
    fi
  fi
  rm -rf "$tmpdir"

  # ── REPORT: derived from what was WRITTEN and what was there before, never
  # from the version token alone.
  local v="$AGENTS_BLOCK_VERSION"
  # `-lt`/`-gt` on an out-of-range integer ERROR and fall through, which turned a
  # downgrade into "refreshed (same version…)". Anything that is not a plain
  # decimal inside int64 range is reported as not comparable, never as current.
  local prev_cmp=""
  case "$prev_version" in
    '') prev_cmp="unversioned" ;;
    *[!0-9]*) prev_cmp="not-a-number" ;;
    *) if [ "${#prev_version}" -le 18 ]; then prev_cmp="$prev_version"; else prev_cmp="too-large"; fi ;;
  esac
  if [ "$state_kind" = "paired" ] && [ "$same_bytes" -eq 1 ]; then
    echo "  ✓ AGENTS.md — Tortoise block v$v (already current)"
  elif [ "$state_kind" = "none" ]; then
    echo "  ✓ AGENTS.md — Tortoise standing instructions created (block v$v)"
  elif [ "$prev_cmp" = "unversioned" ]; then
    echo "  ✓ AGENTS.md — Tortoise block upgraded (unversioned → v$v)"
  elif [ "$prev_cmp" = "not-a-number" ]; then
    echo "  ✓ AGENTS.md — Tortoise block written at v$v (the file's version \"v$prev_version\" is not a number this installer can compare)"
  elif [ "$prev_cmp" = "too-large" ]; then
    echo "  ✓ AGENTS.md — Tortoise block written at v$v (the file's version \"v$prev_version\" is too large to compare safely)"
  elif [ "$prev_cmp" -lt "$v" ]; then
    echo "  ✓ AGENTS.md — Tortoise block upgraded v$prev_version → v$v"
  elif [ "$prev_cmp" -gt "$v" ]; then
    # A pinned/cached/rolled-back installer must not silently DOWNGRADE a
    # project's standing instructions, still less call it an upgrade.
    echo "  ✓ AGENTS.md — Tortoise block written at v$v (the file had v$prev_version: this installer is OLDER than the block it replaced)"
  else
    echo "  ✓ AGENTS.md — Tortoise block refreshed (v$v — same version, updated text)"
  fi
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
  # #4365: onboarding is NOT one of the installed skills — say where it lives,
  # unconditionally (before only the codex harness learned it, via AGENTS.md).
  echo "Onboarding is NOT a skill — it is the instructions your agent reads:"
  echo "   $SKILLS_BASE/tortoise-onboarding/SKILL.md"
  echo ""
  # #4365: an earlier v2 installer wrote tortoise-onboarding into this same
  # namespace. #4327 forbids deleting content we did not write, so it stays —
  # and saying nothing would leave the user with TWO live onboarding artifacts,
  # which is the thing #4365 exists to end.
  if [ -f "$DEST/tortoise-onboarding/SKILL.md" ]; then
    echo "⚠️  A superseded copy is still at $DEST/tortoise-onboarding/SKILL.md"
    echo "    Onboarding is no longer an installed skill; this copy is stale."
    echo "    Remove it:  rm -rf \"$DEST/tortoise-onboarding\""
    echo ""
  fi
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
    # #3429: a refusal is a REAL failure, but the SKILLS are already installed
    # and their banner has printed. Exit 3 separates "AGENTS.md was not upgraded"
    # from "the install broke", and the line below says which.
    emit_codex_agents_block || {
      echo "  (skills above ARE installed; only the AGENTS.md block was not upgraded — exit 3)" >&2
      exit 3
    }
  fi
else
  echo ""
  echo "⚠️  Some skills did not verify in $DEST: ${missing[*]}" >&2
  echo "   Check the directory + permissions, then re-run the installer." >&2
  exit 1
fi
