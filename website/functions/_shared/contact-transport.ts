/**
 * The contact form's delivery seam — issue #2409.
 *
 * ⚠️  OPEN DECISION — THE TRANSPORT IS **NOT SETTLED**: a queue vs. email.
 *
 * The owner has not chosen how a contact submission travels onward. Nothing
 * here may be read as that decision, and nothing outside this file may name a
 * provider or an email sender: the point of this module is that the choice is
 * still OPEN.
 *
 * THE EMAIL LEG WAS REMOVED, AND THIS IS WHY — the product's outbound email
 * sender is ALREADY over budget. `premise-labs#393` records it at 200% of its
 * daily quota on two consecutive days, and its own objective is ZERO
 * quota-rejected sends. Pointing this form at that sender would add a second
 * producer to a saturated sender, and would ship a form that fails exactly when
 * a customer needs it. So this seam DOES NOT SEND EMAIL: it enqueues. That "no
 * send-capable key exists on this host" is a DESIGN SIGNAL, not an ops gap to
 * work around — no credential may be provisioned to restore an email leg.
 *
 * WHAT THIS SEAM IS, THEN — QUEUE-SHAPED. One submission becomes ONE JSON item
 * posted to a configurable intake endpoint, and that is the whole transport:
 *
 *     { name, replyTo, message, receivedAt, source }
 *
 * `enqueue()` is the SOLE entry point. It is the only place in the form's path
 * that performs a network call, and the intake URL is its only configuration.
 * When the transport decision lands, swapping it is a change to THIS FILE ONLY
 * — the item shape and the three-value outcome vocabulary stay as they are.
 *
 * Owner decisions that ARE settled, and stay settled here:
 *   - RECIPIENT: `hello@premiselabs.co` (owner ruling, issue #2409,
 *     2026-09-18). It is a code constant in the caller, never a request field,
 *     and — deliberately — not part of the queued item: the intake endpoint is
 *     the routing point and owns the destination.
 *   - FAIL LOUDLY: a missing intake endpoint is a visible failure
 *     (`status: "not_configured"` → the caller's 503), never a silent success.
 *
 * SECRET HANDLING: there is no secret. The intake endpoint is a plain URL read
 * from `env`; this module sends no credential and no authorization header,
 * because none exists and none may be provisioned while the decision is open.
 */

/**
 * A submission ready to be enqueued. Provider-neutral and destination-free: the
 * caller decides the recipient, the intake endpoint owns routing, and the seam
 * owns the wire item (`receivedAt` and `source` are stamped below).
 */
export interface ContactMessage {
  /** Already control-character-stripped by the caller's validation. */
  name: string;
  /** The submitter's validated address — Reply-To, never the destination. */
  replyTo: string;
  message: string;
}

/** The Cloudflare Pages `env` object, passed through opaquely. */
export interface TransportEnv {
  [key: string]: unknown;
}

/**
 * The seam's whole result vocabulary. The caller maps each case to HTTP; it
 * never learns an upstream status code, error body or endpoint name.
 */
export type IntakeOutcome =
  | { status: "enqueued" }
  | { status: "not_configured" }
  | { status: "failed" };

// ── Current shape: one JSON item, one configurable intake endpoint ─────────

/** The intake endpoint — the seam's ONLY configuration. No credential. */
const INTAKE_URL_ENV = "CONTACT_INTAKE_URL";
/** Which surface produced the item. Lets the endpoint route by producer. */
export const INTAKE_SOURCE = "website/contact";

function envString(env: TransportEnv, name: string): string {
  const v = env[name];
  return typeof v === "string" ? v.trim() : "";
}

/**
 * Enqueue one contact submission to the configured intake endpoint.
 *
 * The configuration gate is HERE, and it precedes the network call: without an
 * intake endpoint there is nowhere to enqueue, and the outcome must be a
 * visible `not_configured` the caller turns into a 503 — never a success that
 * drops the message on the floor. (Pinning the ordering in-source is what the
 * test suite checks; the variable's name is operator-facing only — the visitor
 * message the caller builds never names it.)
 */
export async function enqueue(msg: ContactMessage, env: TransportEnv): Promise<IntakeOutcome> {
  const intakeUrl = envString(env, INTAKE_URL_ENV);
  if (intakeUrl === "") {
    // Visible to an operator: the ABSENCE of the variable is the condition, and
    // naming it is what lets them fix it. No secret value is involved.
    console.error(`contact: ${INTAKE_URL_ENV} is unset — contact form cannot accept submissions`);
    return { status: "not_configured" };
  }

  // The item is built here, whole: exactly the five agreed fields, with the
  // receipt time the seam observed and the producing surface. No request field
  // beyond the validated name/reply-to/message ever reaches the wire.
  const item = {
    name: msg.name,
    replyTo: msg.replyTo,
    message: msg.message,
    receivedAt: new Date().toISOString(),
    source: INTAKE_SOURCE,
  };

  let upstream: Response;
  try {
    upstream = await fetch(intakeUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(item),
    });
  } catch (err) {
    console.error("contact: intake request failed:", err instanceof Error ? err.message : "network error");
    return { status: "failed" };
  }

  if (!upstream.ok) {
    // Log the intake status + message (never a body's contents).
    const detail = await upstream.text().catch(() => "");
    console.error(`contact: intake rejected the submission (${upstream.status}): ${detail.slice(0, 300)}`);
    return { status: "failed" };
  }

  return { status: "enqueued" };
}
