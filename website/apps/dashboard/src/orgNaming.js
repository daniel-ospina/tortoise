// orgNaming.js — JS MIRROR of tortoise/org_naming.py (#2779).
//
// Two layers, two rules:
//   display name — free text (trimmed, whitespace runs collapsed, no control
//                  chars, <= DISPLAY_NAME_MAX). Spaces + non-ASCII survive.
//   identifier   — ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ and not reserved; the key
//                  the graph namespace team_{id} is derived from.
//
// This mirror is pinned to the Python implementation by the SHARED fixture
// tests/fixtures/org_naming_vectors.json, read by BOTH test suites. Any change
// to a rule must update org_naming.py, this file, and the fixture together —
// or the vector tests fail.

export const DISPLAY_NAME_MAX = 64

export const ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/

export const RESERVED_IDENTIFIERS = new Set([
  'registry', 'default', 'system', 'admin', 'api', 'team',
])

// Explicit whitespace class (NOT \s) so this cannot drift from _WS_RE in
// org_naming.py on Unicode whitespace sets.
const WS_RE = /[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g

function isControlChar(ch) {
  if ('\t\n\r\f\v'.includes(ch)) return false // collapsible whitespace
  const c = ch.codePointAt(0)
  return c < 0x20 || (c >= 0x7f && c <= 0x9f)
}

function displayChar(ch) {
  return `U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}`
}

function isAsciiAlnum(ch) {
  return ch !== undefined && /^[A-Za-z0-9]$/.test(ch)
}

// Mirror of validate_display_name: returns the normalized display name, or
// throws a descriptive Error. Callers that want a nullable message use
// displayNameError below.
export function normalizeDisplayName(raw) {
  const text = raw == null ? '' : String(raw)
  for (const ch of text) {
    if (isControlChar(ch)) {
      throw new Error(
        `Invalid organization name — control character ${displayChar(ch)} isn't allowed`,
      )
    }
  }
  const name = text.replace(WS_RE, ' ').replace(/^ +| +$/g, '')
  if (!name) throw new Error('Organization name is required')
  const len = [...name].length
  if (len > DISPLAY_NAME_MAX) {
    throw new Error(
      `Invalid organization name — ${DISPLAY_NAME_MAX} characters or fewer (got ${len})`,
    )
  }
  return name
}

// Nullable-message form of normalizeDisplayName — the shape every UI caller
// wants (and the twin of org_naming.validate_display_name's rejection paths).
export function displayNameError(raw) {
  try {
    normalizeDisplayName(raw)
    return null
  } catch (e) {
    return e.message
  }
}

// Mirror of slugify_id: NFKD -> drop marks -> runs of non-[A-Za-z0-9_-] become
// '-' -> strip leading/trailing '-'/'_' -> truncate 64 -> 'org-' prefix when
// empty/leading-non-alnum/reserved. Case preserved. Pure; never throws.
export function slugifyOrgId(displayName) {
  let text = String(displayName ?? '')
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
  text = text.replace(/[^A-Za-z0-9_-]+/g, '-')
  text = text.replace(/^[-_]+|[-_]+$/g, '')
  text = text.slice(0, DISPLAY_NAME_MAX)
  if (!text || !isAsciiAlnum(text[0]) || RESERVED_IDENTIFIERS.has(text)) {
    text = `org-${text}`
  }
  return text.slice(0, DISPLAY_NAME_MAX)
}

// Mirror of identifier_error: null when legal, else a message naming the FIRST
// offending character (or the reserved word) plus a suggestion. Never the
// generic "letters, numbers, dash, underscore only".
export function orgIdentifierError(candidate) {
  const text = candidate == null ? '' : String(candidate)
  const suggestion = slugifyOrgId(text)
  if (!text) return `Identifier is required. Try: ${suggestion}`
  if (RESERVED_IDENTIFIERS.has(text)) {
    return `Identifier "${text}" is reserved. Try: ${suggestion}`
  }
  const len = [...text].length
  if (len > DISPLAY_NAME_MAX) {
    return `Identifier must be ${DISPLAY_NAME_MAX} characters or fewer (got ${len}). Try: ${suggestion}`
  }
  for (const ch of text) {
    if (ch !== '_' && ch !== '-' && !isAsciiAlnum(ch)) {
      const shown = isControlChar(ch) ? displayChar(ch) : ch
      return `Identifier can't contain "${shown}". Try: ${suggestion}`
    }
  }
  const first = text[0]
  if (!isAsciiAlnum(first)) {
    const shown = isControlChar(first) ? displayChar(first) : first
    return `Identifier can't start with "${shown}". Try: ${suggestion}`
  }
  return null
}
