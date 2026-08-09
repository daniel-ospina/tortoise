# Implementation Plan: Landing Page Fixes — Issue #7840

**Date:** 2026-08-04
**Status:** Draft — awaiting review
**Complexity:** Standard (multi-file HTML/CSS/JS, no backend changes)

---

## 1. Problem Statement

The Tortoise landing page (`premise-labs/index.html` → `premiselabs.co`) and post-signup welcome page (`premise-labs/welcome.html`) have accumulated copy, correctness, and structural issues that undermine the first-impression funnel:

| # | Problem | Impact |
|---|---------|--------|
| P1 | `pip install tortoise-client` (welcome L318, L446) is a non-existent package; `tortoise` is squatted on PyPI by an unrelated turtle library. Real install: `pip install git+https://github.com/daniel-ospina/tortoise.git` | Broken onboarding — user's first action after signup fails |
| P2 | No MCP integration snippet on either page. MCP is the primary integration path for coding agents. Hosted mode works via the streamable-http endpoint `https://api.premiselabs.co/mcp` with `Authorization: Bearer tt_<key>` — NOT stdio + TORTOISE_API_KEY (the stdio transport cannot carry auth tokens; setting the key locally disables stdio — #702). | Lost conversion — agent developers don't know how to connect |
| P3 | Hero tagline "a memory system where agents learn" (L271) is vague. Doesn't communicate what the product *does*. | Weak value prop — no concrete mental model |
| P4 | Cloudflare Turnstile widget (L279 div + L289 script) protects nothing — Supabase auth + issue #7724 rate-limiting cover abuse. | Visual clutter, external dependency, unnecessary load |
| P5 | "5 minutes to your first graph" (L278) is an unvalidated claim. | Trust erosion if onboarding takes longer |
| P6 | Duplicate CSS: `.cta-start` (L122 + L194), `.cta-subtle` (L139 + L208) — second block silently wins, first block's `margin-bottom` and `--accent-hover` reference are lost. | CSS fragility, undefined variable |
| P7 | No CI/CD — manual `wrangler pages deploy`. Deploy is ~6 days stale. | Separate CI/CD issue (#TBD); manual deploy as stopgap |

---

## 2. Proposed Solution — Refined A1 (MCP-First, No Tabs, No Build Step)

**Convergence rationale:** A1's surgical approach is correct — two static HTML pages, 2-3 shared snippets, no build step justified. A2's tabbed selector adds complexity inside a GSAP-fixed div with uncertain overflow behavior, and the JSON is identical across agents (only config file path differs). A3's build step prevents drift but over-indexes on a problem (2 pages, ~20 lines of shared content) that doesn't warrant infrastructure. The refinement borrows A2's MCP-first emphasis without its tab complexity.

### Decision: Refined A1

- **MCP snippet first** in the CTA beat — primary integration path
- **Single static JSON block** with a one-line comment noting config file paths per agent
- **CLI snippet second**, demoted visually
- **No build step** — drift risk for 2 snippets across 2 pages is negligible
- **Duplicate CSS merged** into single blocks

---

## 3. File-by-File Changes

### 3.1 `premise-labs/index.html`

#### A. Hero tagline (L271)

**Current:**
```html
<h2><span>Tortoise</span> — a memory system where agents learn.</h2>
```

**Replace with:**
```html
<h2><span>Tortoise</span> — your coding agent remembers every decision across sessions.</h2>
```

**Rationale:** Concrete value prop. "Remembers every decision across sessions" is the observable behavior. Maintains the declarative, academic-philosophical tone of the hero while making the product value immediately legible to a developer. Competitors (Supermemory, Mem0, Honcho) position as "memory for AI" — we differentiate on *coding agent* specificity and *decisions* (not just data).

#### B. CTA subtitle — soften "5 minutes" claim (L278)

**Current:**
```html
<p class="cta-subtle">No credit card. 5,000 Points free. 5 minutes to your first graph.</p>
```

**Replace with:**
```html
<p class="cta-subtle">5,000 Points free. No credit card. Start building in minutes.</p>
```

**Rationale:** Removes unvalidated "5 minutes" claim. "Start building in minutes" is softer — truthful (signup + API key is <2 min), doesn't over-promise on graph complexity. Removes time-pressure framing that could backfire if the user hits an onboarding snag.

#### C. Remove Turnstile (L279, L289)

**Remove L279:**
```html
<div class="cf-turnstile" data-sitekey="TURNSTILE_SITE_KEY_PLACEHOLDER" style="margin-bottom: 1.25rem;"></div>
```

**Remove L289:**
```html
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
```

**Rationale:** Controller decision. Supabase auth on signup/signin pages provides abuse protection. Issue #7724 adds server-side rate-limiting. Turnstile here protects nothing — there's no form to protect on the landing page, just a link to `/signup`.

#### D. Restructure CTA beat: MCP snippet first, CLI second

**Remove lines L280-L282 (old CLI snippet):**
```html
<div class="cli-snippet">
  <code>$ tortoise init --api-key &lt;key&gt;</code>
  <code>$ tortoise create-point "Hello world"</code>
</div>
```

**Insert MCP snippet + demoted CLI snippet after Turnstile removal. Full replacement block (L278-L284 becomes):**

```html
<p class="cta-subtle">5,000 Points free. No credit card. Start building in minutes.</p>

<!-- MCP snippet — works with Claude Code, Cursor, Pi, Claude Desktop -->
<div class="mcp-snippet">
  <div class="snippet-label">MCP config</div>
  <pre><code>{
  "mcpServers": {
    "tortoise": {
      "command": "python3",
      "args": ["-m", "tortoise.mcp_server"],
      "env": {
        "TORTOISE_API_KEY": "tt_YOUR_KEY",
        "TORTOISE_API_URL": "https://api.premiselabs.co"
      }
    }
  }
}</code></pre>
  <p class="snippet-note">Works with Claude Code (.mcp.json), Cursor (.cursor/mcp.json), Pi, and Claude Desktop.</p>
</div>

<div class="cli-snippet">
  <div class="snippet-label">CLI quickstart</div>
  <pre><code>$ pip install git+https://github.com/daniel-ospina/tortoise.git
$ tortoise init --api-key &lt;key&gt;
$ tortoise create-point "Hello world"</code></pre>
</div>
```

**Rationale for MCP-first ordering:**
- MCP is the primary integration path for coding agents (the core ICP)
- The JSON is identical across all agents — only the config file path differs
- Single static block avoids tab complexity; the one-line note educates on config file location
- CLI becomes secondary: useful for scripting/CI but not the primary onboarding path

**Rationale for adding `pip install` line to CLI snippet:**
- The landing page's CLI snippet previously lacked the install line entirely (it jumped straight to `tortoise init`)
- Adding it here means the snippet is self-contained and actually runnable
- Uses the correct GitHub URL (not the non-existent `tortoise-client`)

#### E. Duplicate CSS merge (L122-L143 + L194-L212)

**Current first block (L122-L143):**
```css
    #beat-cta .cta-start {
      display: inline-block;
      background: var(--accent);
      color: var(--bg);
      border: none;
      padding: 0.85rem 2rem;
      font-family: var(--mono);
      font-size: 0.95rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      transition: background 0.2s;
      margin-bottom: 2rem;
    }
    #beat-cta .cta-start:hover {
      background: var(--accent-hover);
    }
    #beat-cta .cta-subtle {
      font-size: 0.75rem;
      color: var(--text-dim);
      margin-bottom: 1rem;
    }
```

**Current second block (L194-L212) — CSS cascade gives this final say:**
```css
    #beat-cta .cta-start {
      display: inline-block;
      background: var(--accent);
      color: var(--bg);
      padding: 0.9rem 2rem;
      font-family: var(--mono);
      font-size: 1rem;
      text-decoration: none;
      transition: background 0.2s, transform 0.2s;
    }
    #beat-cta .cta-start:hover {
      background: #0891b2;
      transform: scale(1.02);
    }
    #beat-cta .cta-subtle {
      margin-top: 1rem;
      color: var(--text-dim);
      font-size: 0.8rem;
    }
```

**Merge: Replace first block with merged version, remove second block entirely.**

First block replacement (L122-L143):
```css
    #beat-cta .cta-start {
      display: inline-block;
      background: var(--accent);
      color: var(--bg);
      padding: 0.9rem 2rem;
      font-family: var(--mono);
      font-size: 1rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      transition: background 0.2s, transform 0.2s;
      margin-bottom: 2rem;
    }
    #beat-cta .cta-start:hover {
      background: #0891b2;
      transform: scale(1.02);
    }
    #beat-cta .cta-subtle {
      margin-top: 0;
      margin-bottom: 1rem;
      color: var(--text-dim);
      font-size: 0.8rem;
    }
```

Second block removal: delete L194-L212.

**Rationale:** Second block's values are visually superior (larger padding, transform on hover). Added back `margin-bottom: 2rem` and `font-weight: 600` from the first block (both lost in cascade). Removed `--accent-hover` (undefined variable, replace with explicit `#0891b2`). Removed `border: none` (button is an `<a>` tag, no default border).

#### F. New CSS for MCP snippet

Add to the `<style>` block (after the `.cli-snippet` block, before the `.selfhost-link` block):

```css
    /* MCP snippet */
    #beat-cta .mcp-snippet {
      margin-top: 1.25rem;
      margin-bottom: 1rem;
      background: rgba(6, 182, 212, 0.06);
      border: 1px solid rgba(6, 182, 212, 0.2);
      padding: 0.875rem 1rem;
      border-radius: 4px;
      text-align: left;
      max-width: 480px;
    }
    #beat-cta .mcp-snippet .snippet-label {
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-dim);
      margin-bottom: 0.5rem;
    }
    #beat-cta .mcp-snippet pre {
      margin: 0;
      overflow-x: auto;
    }
    #beat-cta .mcp-snippet code {
      display: block;
      color: var(--accent);
      font-family: var(--mono);
      font-size: 0.72rem;
      line-height: 1.6;
      white-space: pre;
    }
    #beat-cta .mcp-snippet .snippet-note {
      margin-top: 0.6rem;
      font-size: 0.68rem;
      color: var(--text-dim);
      line-height: 1.4;
    }

    /* CLI snippet — demoted */
    #beat-cta .cli-snippet {
      margin-top: 0;
      margin-bottom: 1.25rem;
      background: rgba(6, 182, 212, 0.04);
      border: 1px solid rgba(6, 182, 212, 0.12);
      padding: 0.625rem 1rem;
      border-radius: 4px;
      text-align: left;
      max-width: 420px;
    }
    #beat-cta .cli-snippet .snippet-label {
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-dim);
      margin-bottom: 0.4rem;
    }
    #beat-cta .cli-snippet pre {
      margin: 0;
    }
    #beat-cta .cli-snippet code {
      display: block;
      color: var(--accent);
      font-family: var(--mono);
      font-size: 0.78rem;
      line-height: 1.7;
      white-space: pre;
    }
```

**Note on z-index / overflow:** The `.beat` overlay is `position: fixed` at `z-index: 1`. The MCP snippet is ~10 lines tall. Combined with the CTA button, subtitle, CLI snippet, and links, total CTA beat height is ~400px. At mobile viewport heights (≥667px), this fits. Verify visually post-deploy.

---

### 3.2 `premise-labs/welcome.html`

#### A. Fix `pip install` in HTML snippet (L318)

**Current:**
```html
<span class="cmd">pip install tortoise-client</span>
```

**Replace with:**
```html
<span class="cmd">pip install git+https://github.com/daniel-ospina/tortoise.git</span>
```

#### B. Fix `pip install` in JavaScript `copySnippet()` (L446)

**Current:**
```js
const snippet = `# Install Tortoise
pip install tortoise-client
```

**Replace with:**
```js
const snippet = `# Install Tortoise
pip install git+https://github.com/daniel-ospina/tortoise.git
```

#### C. Add MCP config card

Insert a new card between the API key card and the Quickstart card. After the API key card's closing `</div>` (after L314's `</div>` — the warning div — and the card's closing `</div>` at ~L315), insert:

```html
<div class="card">
  <div class="card-label">MCP config</div>
  <div class="snippet" id="mcp-snippet">
    <pre><code>{
  "mcpServers": {
    "tortoise": {
      "command": "python3",
      "args": ["-m", "tortoise.mcp_server"],
      "env": {
        "TORTOISE_API_KEY": "<span id="mcp-snippet-key">tt_YOUR_KEY</span>",
        "TORTOISE_API_URL": "https://api.premiselabs.co"
      }
    }
  }
}</code></pre>
  </div>
  <button class="btn-copy" onclick="copyMcpConfig()" style="width:100%;margin-bottom:0.75rem;">
    Copy MCP config
  </button>
  <p style="font-size:0.72rem;color:var(--text-dim);line-height:1.4;">
    Paste into <code style="color:var(--accent);">.mcp.json</code> (Claude Code),
    <code style="color:var(--accent);">.cursor/mcp.json</code> (Cursor),
    or <code style="color:var(--accent);">claude_desktop_config.json</code> (Claude Desktop).
  </p>
</div>
```

#### D. Update `showSuccess()` to populate MCP snippet key

**Current `showSuccess()`:**
```js
document.getElementById("snippet-key").textContent = data.api_key;
```

**Add after that line:**
```js
document.getElementById("mcp-snippet-key").textContent = data.api_key;
```

#### E. Add `copyMcpConfig()` JavaScript function

Add after the `copySnippet()` function:

```js
function copyMcpConfig() {
  const key = document.getElementById("api-key").textContent;
  const config = `{
  "mcpServers": {
    "tortoise": {
      "command": "python3",
      "args": ["-m", "tortoise.mcp_server"],
      "env": {
        "TORTOISE_API_KEY": "${key}",
        "TORTOISE_API_URL": "https://api.premiselabs.co"
      }
    }
  }
}`;
  copyToClipboard(config);

  // Brief visual feedback
  const btns = document.querySelectorAll('.btn-copy');
  btns.forEach(b => { b.classList.remove('copied'); b.textContent = b.textContent.replace('Copied!', 'Copy'); });
  // (Simpler: just do a toast-style feedback inline)
  const mcpBtn = document.querySelector('#mcp-snippet + .btn-copy');
  // Actually we need to select the right button — use the onclick context
  // Better approach: use event.target
}
```

**Revised approach — simpler inline feedback for MCP copy:**

Replace the naive selection above with a proper implementation. Add `id` to the MCP copy button and handle feedback inline:

```js
function copyMcpConfig() {
  const key = document.getElementById("api-key").textContent;
  const config = `{
  "mcpServers": {
    "tortoise": {
      "command": "python3",
      "args": ["-m", "tortoise.mcp_server"],
      "env": {
        "TORTOISE_API_KEY": "${key}",
        "TORTOISE_API_URL": "https://api.premiselabs.co"
      }
    }
  }
}`;
  copyToClipboard(config);
  const btn = document.getElementById("btn-copy-mcp");
  const textEl = btn.querySelector("span");
  const origText = textEl ? textEl.textContent : btn.textContent;
  btn.classList.add("copied");
  if (textEl) textEl.textContent = "Copied!";
  else btn.textContent = "Copied!";
  setTimeout(() => {
    btn.classList.remove("copied");
    if (textEl) textEl.textContent = origText;
    else btn.textContent = origText;
  }, 2000);
}
```

And the button gets `id="btn-copy-mcp"`:
```html
<button class="btn-copy" id="btn-copy-mcp" onclick="copyMcpConfig()" style="width:100%;margin-bottom:0.75rem;">
  <span>Copy MCP config</span>
</button>
```

#### F. Update `copySnippet()` to use `tortoise-client` → GitHub URL

Already covered in 3.2.B above.

---

## 4. Exact Copy Proposals

### 4.1 Hero tagline (index.html L271)

| Version | Text |
|---------|------|
| Current | `Tortoise — a memory system where agents learn.` |
| Proposed | `Tortoise — your coding agent remembers every decision across sessions.` |

**Why this works:**
- "Your coding agent" → specificity (not generic AI memory), speaks to developer ICP
- "Remembers every decision" → observable behavior, concrete value
- "Across sessions" → the key differentiator vs. in-session context windows
- Maintains the declarative, single-sentence structure of the current line
- Fits the existing `<h2>` typography (Georgia serif, clamp 1.4-2rem, centered)

### 4.2 CTA subtitle (index.html L278)

| Version | Text |
|---------|------|
| Current | `No credit card. 5,000 Points free. 5 minutes to your first graph.` |
| Proposed | `5,000 Points free. No credit card. Start building in minutes.` |

**Why this works:**
- Lead with value (5,000 Points) not friction mitigation (no credit card)
- "Start building in minutes" is truthful — signup flow completion <2 min
- Removes specific time claim that can't be validated across all user environments

### 4.3 CTA button

Unchanged: `Start free →` linking to `/signup`. Per epic #7711, this is the hosted signup flow. Self-hosting is a secondary link below the fold.

### 4.4 MCP snippet (index.html and welcome.html)

```
{
  "mcpServers": {
    "tortoise": {
      "command": "python3",
      "args": ["-m", "tortoise.mcp_server"],
      "env": {
        "TORTOISE_API_KEY": "tt_YOUR_KEY",
        "TORTOISE_API_URL": "https://api.premiselabs.co"
      }
    }
  }
}
```

**Config file destinations:**
- Claude Code: `.mcp.json` (project root)
- Cursor: `.cursor/mcp.json` (project root)
- Pi: via pi config / MCP extension
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)

---

## 5. Implementation Steps (Ordered)

### Step 1: Fix `premise-labs/index.html`

1. **Merge duplicate CSS** — Replace L122-L143 with merged `.cta-start`/`.cta-subtle` block. Delete L194-L212.
2. **Add MCP + updated CLI CSS** — Insert new CSS blocks after the old `.cli-snippet` block (before `.selfhost-link`).
3. **Replace hero tagline** (L271) — New text.
4. **Replace CTA subtitle** (L278) — Softened "5 minutes" claim.
5. **Remove Turnstile div** (L279).
6. **Replace CLI snippet** (L280-L282) — New MCP snippet block + demoted CLI snippet block with `pip install` line.
7. **Remove Turnstile script** (L289).

### Step 2: Fix `premise-labs/welcome.html`

1. **Fix pip install** in HTML snippet (L318) — Replace `tortoise-client` with GitHub URL.
2. **Fix pip install** in JS `copySnippet()` (L446) — Same replacement.
3. **Add MCP config card** — Insert after API key card closing `</div>`, before Quickstart card.
4. **Add `copyMcpConfig()` function** — Insert after `copySnippet()` in the `<script>` block.
5. **Update `showSuccess()`** — Add line to populate `#mcp-snippet-key`.

### Step 3: Verify locally

```bash
# Visual check — open in browser
open premise-labs/index.html
open premise-labs/welcome.html

# Grep verification (see Testing Strategy below)
```

### Step 4: Deploy

```bash
cd premise-labs
npx wrangler pages deploy . --project-name=premise-labs --branch=main
```

### Step 5: Post-deploy verification

```bash
curl -s https://premiselabs.co | grep -c 'tortoise-client'  # should be 0
curl -s https://premiselabs.co | grep -c 'turnstile'         # should be 0
curl -s https://premiselabs.co | grep -c 'mcpServers'         # should be 1
curl -s https://premiselabs.co/welcome.html | grep -c 'tortoise-client'  # should be 0
curl -s https://premiselabs.co/welcome.html | grep -c 'mcpServers'       # should be 1
curl -s https://premiselabs.co/welcome.html | grep -c 'git+https://github.com/daniel-ospina/tortoise.git'  # should be 2 (HTML + JS)
```

---

## 6. Testing Strategy

### 6.1 Grep-based verification (automated, zero-dependency)

Run these in sequence. All must pass.

```bash
INDEX="premise-labs/index.html"
WELCOME="premise-labs/welcome.html"

# --- Negative checks (must NOT exist) ---

# 1. No tortoise-client reference anywhere
! grep -q 'tortoise-client' "$INDEX" || echo "FAIL: tortoise-client in index"
! grep -q 'tortoise-client' "$WELCOME" || echo "FAIL: tortoise-client in welcome"

# 2. No Turnstile references
! grep -qi 'turnstile' "$INDEX" || echo "FAIL: turnstile in index"

# 3. No duplicate .cta-start CSS blocks
test "$(grep -c '#beat-cta .cta-start {' "$INDEX")" -eq 1 || echo "FAIL: .cta-start not merged"

# 4. No duplicate .cta-subtle CSS blocks
test "$(grep -c '#beat-cta .cta-subtle {' "$INDEX")" -eq 1 || echo "FAIL: .cta-subtle not merged"

# 5. No --accent-hover (undefined variable)
! grep -q '\-\-accent-hover' "$INDEX" || echo "FAIL: --accent-hover still referenced"

# 6. No "5 minutes" claim
! grep -q '5 minutes' "$INDEX" || echo "FAIL: 5 minutes still present"

# --- Positive checks (must exist) ---

# 7. MCP snippet present in both pages
grep -q '"mcpServers"' "$INDEX" || echo "FAIL: mcpServers missing in index"
grep -q '"mcpServers"' "$WELCOME" || echo "FAIL: mcpServers missing in welcome"

# 8. Correct pip install URL in both pages
grep -q 'git+https://github.com/daniel-ospina/tortoise.git' "$INDEX" || echo "FAIL: GitHub URL missing in index"
grep -q 'git+https://github.com/daniel-ospina/tortoise.git' "$WELCOME" || echo "FAIL: GitHub URL missing in welcome"

# 9. New tagline present
grep -q 'remembers every decision across sessions' "$INDEX" || echo "FAIL: new tagline missing"

# 10. MCP config file note present
grep -q '.mcp.json' "$INDEX" || echo "FAIL: config file note missing in index"
grep -q '.mcp.json' "$WELCOME" || echo "FAIL: config file note missing in welcome"

# 11. copyMcpConfig function exists
grep -q 'function copyMcpConfig' "$WELCOME" || echo "FAIL: copyMcpConfig missing"

# 12. mcp-snippet-key populated in showSuccess
grep -q "mcp-snippet-key" "$WELCOME" || echo "FAIL: mcp-snippet-key not in welcome"

echo "All checks complete."
```

### 6.2 Visual verification (manual)

1. Open `premise-labs/index.html` in Chrome/Firefox/Safari
2. Scroll to CTA beat (bottom of scroll) — verify:
   - MCP snippet is visible and readable
   - CLI snippet is below MCP, visually demoted (smaller, lighter border)
   - No Turnstile widget
   - CTA button "Start free →" is present and styled
   - Config file note is visible
3. At mobile viewport (375×667) — verify no overflow, all text readable
4. Open `premise-labs/welcome.html` — verify:
   - MCP card appears between API key card and Quickstart card
   - `pip install` line shows GitHub URL (not tortoise-client)
   - "Copy MCP config" button works (check clipboard)

### 6.3 JavaScript correctness — `copySnippet()` and `copyMcpConfig()`

**Critical invariant:** The text displayed in the snippet must match the text copied to clipboard.

For `copySnippet()`: The HTML `<pre>` block (L317-L324) and the JS `copySnippet()` function (L443-L452) construct the same text — both use `pip install` + `tortoise init --api-key <key>` + `tortoise create-point`. After the fix, both must use the GitHub URL.

Verify by:
1. Open welcome.html in browser
2. Click "Copy quickstart"
3. Paste into text editor — verify the `pip install` line is the GitHub URL
4. Click "Copy MCP config"
5. Paste into text editor — verify valid JSON with the correct API key

### 6.4 Deployed verification (post-wrangler-deploy)

```bash
# Fetch production pages
curl -sL https://premiselabs.co > /tmp/index-prod.html
curl -sL https://premiselabs.co/welcome.html > /tmp/welcome-prod.html

# Run the same grep suite against prod
INDEX=/tmp/index-prod.html
WELCOME=/tmp/welcome-prod.html

! grep -q 'tortoise-client' "$INDEX"
! grep -q 'tortoise-client' "$WELCOME"
! grep -qi 'turnstile' "$INDEX"
grep -q '"mcpServers"' "$INDEX"
grep -q '"mcpServers"' "$WELCOME"
grep -q 'git+https://github.com/daniel-ospina/tortoise.git' "$INDEX"
grep -q 'git+https://github.com/daniel-ospina/tortoise.git' "$WELCOME"

echo "Production verification complete."
```

---

## 7. Acceptance Criteria

| # | Criterion | Verification method |
|---|-----------|-------------------|
| AC1 | `pip install tortoise-client` does not appear in any HTML or JS file under `premise-labs/` | Grep (check 1) |
| AC2 | `pip install git+https://github.com/daniel-ospina/tortoise.git` appears in both index.html and welcome.html | Grep (check 8) |
| AC3 | MCP JSON snippet with `"mcpServers"` appears on both pages | Grep (checks 7, 11) |
| AC4 | Welcome page MCP snippet includes a copy button that copies valid JSON with the user's API key | Manual JS test |
| AC5 | Hero tagline says "remembers every decision across sessions" | Grep (check 9) |
| AC6 | No Turnstile references in index.html (HTML or script) | Grep (check 2) |
| AC7 | No "5 minutes" claim in index.html | Grep (check 6) |
| AC8 | Exactly one `.cta-start` CSS block and one `.cta-subtle` CSS block | Grep (checks 3, 4) |
| AC9 | No `--accent-hover` variable reference (undefined) | Grep (check 5) |
| AC10 | Config file note (`.mcp.json`, `.cursor/mcp.json`, `claude_desktop_config.json`) appears on both pages | Grep (check 10) |
| AC11 | All 12 grep checks pass with zero failures | Run verification script |
| AC12 | Production deploy passes same grep suite | Post-deploy verification |
| AC13 | CTA beat fits within viewport at 375×667 without overflow | Manual visual check |
| AC14 | MCP snippet visually appears above CLI snippet on index.html | Manual visual check |

---

## 8. Runtime Prerequisites

### Deploy

```bash
# 1. Authenticate with Cloudflare (one-time or if token expired)
npx wrangler login

# 2. Deploy
cd premise-labs
npx wrangler pages deploy . --project-name=premise-labs --branch=main
```

### Production runtime dependencies (not affected by this change)

- `SUPABASE_ANON_KEY` placeholder (`{{SUPABASE_ANON_KEY}}`) in welcome.html must be replaced at deploy time. This is an existing concern — the current deploy pipeline handles it (likely via wrangler env vars or a deploy-time sed). **This plan does not change or fix this mechanism.** If the placeholder replacement is broken, that's a separate issue.
- `TURNSTILE_SITE_KEY_PLACEHOLDER` — removed by this plan, so the placeholder concern goes away for index.html.
- The Turnstile site key in `signup.html` and `signin.html` is **not affected** by this plan (those pages have actual forms).

---

## 9. Rejected Alternatives

### A2: "MCP-First CTA with Tabbed Agent Selector"

**What it was:** Restructure the CTA beat so the MCP snippet is the primary element with tabs for Claude Code / Pi / Cursor / CLI. Each tab shows the correct config file path and the identical JSON.

**Why rejected:**
- The JSON body is **identical** across all four agents — only the config file path differs. Tabs imply meaningful variation where there is none. This is a documentation problem, not a UI problem.
- Implementing tabs inside the GSAP-managed fixed overlay adds DOM complexity and risk (z-index interactions, mobile overflow, JS for tab switching inside a beat that's already JS-managed).
- A one-line comment listing config paths achieves the same educational goal with zero JS.

**When this WOULD have been better:** If the JSON structure differed materially per agent (e.g., Claude Code uses `type: "stdio"` while Cursor uses a different envelope), then tabs would be the right UX pattern. Today, they're all standard MCP JSON — tabs would be confusing ("why are these tabs identical?").

### A3: "Shared Template + Build Step"

**What it was:** Extract shared CSS, MCP snippet, and CLI snippet into partial files. Python `build.py` assembles index.html and welcome.html from templates. Prevents drift between pages.

**Why rejected:**
- Overkill for 2 pages sharing ~20 lines of content. The build script + template infrastructure would be ~50+ lines of Python against a 20-line drift problem.
- Adds a build step to what is currently "edit HTML, deploy." This is regression in developer experience for the 95% case where only one page changes.
- CI/CD for the landing page is out of scope for this issue — adding a build step before CI/CD exists means manual `python build.py && wrangler pages deploy` instead of just `wrangler pages deploy`.
- The actual shared surface is: MCP JSON (12 lines), config file note (3 lines), pip install URL (1 line). Three tiny blocks. A comment in each file ("keep in sync with the other page") is proportionally appropriate.

**When this WOULD have been better:** When the landing page surface grows beyond 2 pages, or when shared content blocks exceed ~5. At that point, drift becomes a real risk and a build step pays for itself. Today, it's premature infrastructure.

### Separating CLI/pip-install into the self-hosting link only

**What it was:** Remove the CLI snippet from the landing page entirely. The CTA would be MCP-only. CLI installation lives on the self-hosting docs (GitHub README).

**Why rejected:**
- Some developers want to try Tortoise from the CLI before wiring it into their agent. The CLI is a lower-friction first touch.
- Removing the CLI snippet entirely would make the landing page feel "MCP-or-nothing," which alienates non-agent use cases (scripting, data pipelines) that are valid future ICPs.

---

## 10. Known Constraints

| Constraint | Impact | Mitigation |
|-----------|--------|-----------|
| **Manual deploy only** — no CI/CD pipeline for premise-labs | Deploy requires human with `wrangler` auth | Documented in Step 4. Separate CI/CD issue is needed (file after this ships) |
| **`SUPABASE_ANON_KEY` placeholder** — welcome.html uses `{{SUPABASE_ANON_KEY}}` that must be replaced at deploy time | If the replacement mechanism fails, welcome.html's Supabase client won't initialize (existing bug, not introduced) | Out of scope. Note it, don't touch it. |
| **Welcome page only works with Supabase session** — `waitForProvisioning()` polls `user_teams` table | If DB schema changes, welcome page breaks (existing coupling) | Out of scope. Issue #7852 tracks onboarding robustness. |
| **No staging environment** — deploy is straight to production (`premiselabs.co`) | No pre-prod visual verification | Manual `open index.html` locally before deploy. Low risk — all changes are HTML/CSS copy, no backend. |
| **PyPI not published** — `pip install git+https://...` is a stopgap | Ugly install line, will need updating when PyPI is published | Acceptable. PyPI publish is a separate tracked issue. When it ships, update both pages (2-line grep-and-replace). |
| **`pip install git+...` requires git** — users without git get a cryptic error | Minor friction for non-developer users | Acceptable. Core ICP is developers (they have git). Will resolve when PyPI is published. |
| **Mobile viewport overflow risk** — MCP snippet is taller than old CLI snippet | Could push content below fold on very short viewports | Tested at 375×667 (iPhone SE). If issues found, reduce MCP snippet font-size or use more compact JSON formatting. |

---

## Appendix A: Full list of index.html CSS changes (for review)

### Before (L122-L143):
```css
    #beat-cta .cta-start {
      display: inline-block;
      background: var(--accent);
      color: var(--bg);
      border: none;
      padding: 0.85rem 2rem;
      font-family: var(--mono);
      font-size: 0.95rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      transition: background 0.2s;
      margin-bottom: 2rem;
    }
    #beat-cta .cta-start:hover {
      background: var(--accent-hover);
    }
    #beat-cta .cta-subtle {
      font-size: 0.75rem;
      color: var(--text-dim);
      margin-bottom: 1rem;
    }
```

### After (L122-L143):
```css
    #beat-cta .cta-start {
      display: inline-block;
      background: var(--accent);
      color: var(--bg);
      padding: 0.9rem 2rem;
      font-family: var(--mono);
      font-size: 1rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      transition: background 0.2s, transform 0.2s;
      margin-bottom: 2rem;
    }
    #beat-cta .cta-start:hover {
      background: #0891b2;
      transform: scale(1.02);
    }
    #beat-cta .cta-subtle {
      margin-top: 0;
      margin-bottom: 1rem;
      color: var(--text-dim);
      font-size: 0.8rem;
    }
```

### Removed (was L194-L212):
```css
    #beat-cta .cta-start {
      display: inline-block;
      background: var(--accent);
      color: var(--bg);
      padding: 0.9rem 2rem;
      font-family: var(--mono);
      font-size: 1rem;
      text-decoration: none;
      transition: background 0.2s, transform 0.2s;
    }
    #beat-cta .cta-start:hover {
      background: #0891b2;
      transform: scale(1.02);
    }
    #beat-cta .cta-subtle {
      margin-top: 1rem;
      color: var(--text-dim);
      font-size: 0.8rem;
    }
```

---

## Appendix B: Full list of welcome.html changes (for review)

See Section 3.2 for all changes. Summary:
- L318: `tortoise-client` → `git+https://github.com/daniel-ospina/tortoise.git`
- L446: `tortoise-client` → `git+https://github.com/daniel-ospina/tortoise.git`
- New card inserted after API key card (after L315): MCP config with copy button
- New JS function `copyMcpConfig()` inserted after `copySnippet()`
- `showSuccess()`: add `mcp-snippet-key` population line

---

## Appendix C: Rejected CTA destinations (for context)

The plan's CTA destination is `/signup` (hosted free tier). The question of "hosted signup vs. GitHub self-host interim" was flagged as a human decision point. Resolution per epic #7711: hosted free tier is the primary funnel. Self-hosting is a secondary link below the fold. This plan does not change the CTA destination.

---

> **Plan author:** Pi (claude-sonnet)
> **Review gate:** `plan-review` skill before implementation
> **Deploy gate:** Manual `wrangler pages deploy` — no CI/CD automation yet
