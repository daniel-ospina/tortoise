// orgNaming.ts — Deno/TS MIRROR of the org display-name / identifier rules
// (#2779). The canonical Python implementation is tortoise/org_naming.py and
// the dashboard mirror is website/apps/dashboard/src/orgNaming.js; all three
// are pinned together by the shared fixture
// tests/fixtures/org_naming_vectors.json. Change any rule in all three places
// + the fixture, or the vector tests fail.
//
// This module exists because an Edge Function cannot import the Python module;
// it is a copy of the same predicate, not a second source of truth.

export const DISPLAY_NAME_MAX = 64

export const RESERVED_IDENTIFIERS = new Set([
  'registry', 'default', 'system', 'admin', 'api', 'team',
])

// Explicit whitespace class (NOT \s) so this cannot drift from _WS_RE in
// org_naming.py on Unicode whitespace sets.
const WS_RE = /[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g

export function isControlChar(ch: string): boolean {
  if ('\t\n\r\f\v'.includes(ch)) return false // collapsible whitespace
  const c = ch.codePointAt(0) as number
  return c < 0x20 || (c >= 0x7f && c <= 0x9f)
}

export function displayChar(ch: string): string {
  return `U+${(ch.codePointAt(0) as number).toString(16).toUpperCase().padStart(4, '0')}`
}

// Mirror of validate_display_name: returns the normalized display name, or
// null when the input is not a legal display name (blank / >64 / control
// char). Never throws.
export function normalizeDisplayName(raw: unknown): string | null {
  const text = raw == null ? '' : String(raw)
  for (const ch of text) {
    if (isControlChar(ch)) return null
  }
  const name = text.replace(WS_RE, ' ').replace(/^ +| +$/g, '')
  if (!name) return null
  if ([...name].length > DISPLAY_NAME_MAX) return null
  return name
}

// Mirror of slugify_id: NFKD -> drop marks -> runs of non-[A-Za-z0-9_-] become
// '-' -> strip leading/trailing '-'/'_' -> truncate 64 -> 'org-' prefix when
// empty/leading-non-alnum/reserved. Case preserved. Pure; never throws.
export function slugifyOrgId(displayName: unknown): string {
  let text = String(displayName ?? '')
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
  text = text.replace(/[^A-Za-z0-9_-]+/g, '-')
  text = text.replace(/^[-_]+|[-_]+$/g, '')
  text = text.slice(0, DISPLAY_NAME_MAX)
  if (!text || !/^[A-Za-z0-9]$/.test(text[0]) || RESERVED_IDENTIFIERS.has(text)) {
    text = `org-${text}`
  }
  return text.slice(0, DISPLAY_NAME_MAX)
}
