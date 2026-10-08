// limitContact.js — #5425: the human route for a customer at a ceiling.
//
// Owner ruling (2026-10-08): "we should just have rate-limits and if they want
// more they need to speak with us". The route for a customer at a ceiling is a
// CONVERSATION.
//
// ⚠️ This is the JS twin of `tortoise/quota.py::with_limit_contact`. The server
// already appends the same sentence to its 402/409 `detail`, but the dashboard
// REPLACES that detail with its own copy on the limit surfaces — so without
// this the one place a customer actually hits the ceiling was the one place the
// route was missing (a reviewer caught exactly that in round 4, after the
// Python side had been declared complete). Two copies of one sentence is a
// drift risk we accept because there is no API for it; the test in
// limitContact.test.js pins the address, and the Python guard pins its side.

export const LIMIT_CONTACT = ' Need more? Contact support@premiselabs.co.'

/** Terminate `text` as a sentence and append the contact route. */
export function withLimitContact(text) {
  const base = (text || '').replace(/\s+$/, '')
  // Degenerate input returns the constant VERBATIM, leading space included, so
  // this seam is byte-identical to `tortoise.quota.with_limit_contact` for every
  // input. They previously diverged here (JS trimmed, Python did not), which is
  // exactly the drift a twin cannot afford: the parity is now pinned by
  // limitContact.test.js reading the Python constant out of the source.
  if (!base) return LIMIT_CONTACT
  const terminated = /[.!?]$/.test(base) ? base : `${base}.`
  return `${terminated}${LIMIT_CONTACT}`
}
