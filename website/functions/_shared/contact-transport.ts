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
 * WHY THIS FORM IS QUEUE-SHAPED — THE OWNER'S ARCHITECTURE, IN HIS TERMS:
 * WE RECEIVE. Customers email `hello@premiselabs.co` and
 * `support@premiselabs.co`, and those messages are processed automatically
 * through intake; NO REPLY IS REQUIRED for the product to work, and for most
 * use cases we should not be sending email at all. Outbound email has exactly
 * TWO legitimate homes — replying to a user who emailed us first, and AUTH
 * FLOWS (email+password login/sign-up), where sending ourselves is deliberate
 * because Supabase's auth mail would max its quota and arrives from Supabase,
 * which is confusing to a new sign-up. So this form is an INTAKE PRODUCER, not
 * an email sender: RECEIVING IS THE MECHANISM AND SENDING IS THE EXCEPTION.
 * It routes INTO intake. No send-capable credential is provisioned for it —
 * not because sending is forbidden, but because the queue leg needs none.
 *
 * ⛔ `premise-labs#393` IS A BUDGET TO MANAGE, NOT A REASON TO REFUSE TO
 * BUILD. #393's objective is ZERO quota-rejected sends; the sender's quota is
 * MANAGED against that objective. It is not a constraint that forbids a second
 * producer. An earlier revision of this header recorded the email leg's
 * removal as quota-avoidance (#393 "at 200% of quota") — that framing is
 * RETRACTED and must not be reinstated: never cite the quota as a reason not
 * to build. If the transport decision lands as email, provisioning that leg is
 * the owner's call, while the decision is open nothing here names or reads a
 * provider credential.
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
 * because the queue leg needs none and none may be provisioned while the
 * transport decision is open (do not read that absence as a quota verdict).
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
