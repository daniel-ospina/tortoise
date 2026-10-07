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
 * It routes INTO intake. No SEND-capable credential is provisioned for it —
 * not because sending is forbidden, but because the queue leg needs no way to
 * send (it carries the intake's own inbound secret; see the correction below).
 *
 * ── WIRED AT THE EXISTING INTAKE (relay ruling, tortoise #2409) ─────────────
 * The endpoint is the intake that already exists — one intake, many producers
 * (premise-labs #426). Its contract, read from the function itself
 * (`swarm/supabase/functions/inbound-ingest/index.ts`), is:
 *
 *     POST <org-data>/functions/v1/inbound-ingest
 *     x-inbound-secret: <INBOUND_SECRET_<SOURCE>>      (uppercased source)
 *     { source, source_item_id, payload }
 *
 * and it answers `403 unknown_source` when no `INBOUND_SECRET_<SOURCE>` is set
 * for that source, `401 unauthorized` when the secret does not match, and
 * dedupes on `(source, source_item_id)`. Three consequences are load-bearing
 * and were NOT obvious before reading it:
 *
 *   1. THE QUEUE LEG DOES CARRY A SECRET — the intake's own inbound shared
 *      secret. It is not a send-capable credential and it cannot send mail; the
 *      earlier UNQUALIFIED note here that this leg “needs none” was premised on
 *      a credential-free intake, and it is corrected rather than deleted (the
 *      qualified claim — no send-capable credential — still holds and is the
 *      one that matters).
 *   2. THE SOURCE NAME MUST BE ENV-NAME-SAFE. The intake derives the variable
 *      name by uppercasing the source, so `website/contact` would ask for
 *      `INBOUND_SECRET_WEBSITE/CONTACT` — not a name any platform can set. It
 *      is `website_contact`.
 *   3. THE ITEM IS AN ENVELOPE, NOT A BARE OBJECT — `payload` carries the
 *      submission, and `source_item_id` must be unique per source.
 *
 * Absent configuration is still a VISIBLE failure, and it now covers both
 * variables: a URL with no secret would be answered 401/403 by the intake, so
 * reporting `enqueued` for a message nothing will read is not available here.
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
 * in the intake's envelope, posted to the configured intake endpoint, and that is
 * the whole transport:
 *
 *     { source, source_item_id, payload: { subject, body, name, replyTo,
 *                                         message, receivedAt } }
 *
 * `enqueue()` is the SOLE entry point. It is the only place in the form's path
 * that performs a network call, and its two variables (the endpoint and the
 * intake's inbound secret) are its only configuration. When the email leg is
 * decided, swapping it is a change to THIS FILE ONLY — the envelope and the
 * three-value outcome vocabulary stay as they are.
 *
 * Owner decisions that ARE settled, and stay settled here:
 *   - RECIPIENT: `hello@premiselabs.co` (owner ruling, issue #2409,
 *     2026-09-18). It is a code constant in the caller, never a request field,
 *     and — deliberately — not part of the queued item: the intake endpoint is
 *     the routing point and owns the destination.
 *   - FAIL LOUDLY: a missing intake endpoint OR secret is a visible failure
 *     (`status: "not_configured"` → the caller's 503), never a silent success.
 *
 * SECRET HANDLING: the only secret is the intake's inbound shared secret,
 * read from `env.CONTACT_INTAKE_SECRET` and sent as the `x-inbound-secret`
 * header the intake requires. It is provisioned by an operator (never in this
 * repository, never in a request body) and it grants nothing but "submit an
 * item to this intake": it cannot send mail, read the intake, or reach any
 * other service. No provider key and no send-capable credential is read here.
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

/** The intake endpoint. */
const INTAKE_URL_ENV = "CONTACT_INTAKE_URL";
/**
 * The intake's inbound shared secret, sent as `x-inbound-secret`. Required by
 * the endpoint, not by us: without it the intake answers 401/403 and the
 * message is dropped, so its absence is `not_configured` — the same visible
 * failure as an absent URL.
 */
const INTAKE_SECRET_ENV = "CONTACT_INTAKE_SECRET";
/**
 * Which surface produced the item. The intake derives its per-source secret
 * variable by uppercasing this (`INBOUND_SECRET_${source.toUpperCase()}`), so it
 * must survive as an environment-variable name: no slash, no space. It is
 * deliberately `website_contact` and not `website/contact`.
 */
export const INTAKE_SOURCE = "website_contact";

function envString(env: TransportEnv, name: string): string {
  const v = env[name];
  return typeof v === "string" ? v.trim() : "";
}

/**
 * Enqueue one contact submission to the configured intake endpoint.
 *
 * The configuration gate is HERE, and it precedes the network call: without an
 * endpoint AND the intake's secret there is nowhere to enqueue, and the outcome
 * must be a visible `not_configured` the caller turns into a 503 — never a
 * success that drops the message on the floor. (Pinning the ordering in-source
 * is what the test suite checks; the variables' names are operator-facing only —
 * the visitor message the caller builds never names them.)
 */
export async function enqueue(msg: ContactMessage, env: TransportEnv): Promise<IntakeOutcome> {
  const intakeUrl = envString(env, INTAKE_URL_ENV);
  const intakeSecret = envString(env, INTAKE_SECRET_ENV);
  if (intakeUrl === "" || intakeSecret === "") {
    // Visible to an operator: the ABSENCE of a variable is the condition, and
    // naming it is what lets them fix it. Neither value is a secret being
    // logged — one is a URL, the other only ever named, never printed.
    console.error(
      "contact: contact form cannot accept submissions — missing " +
        [INTAKE_URL_ENV, INTAKE_SECRET_ENV].filter((n) => envString(env, n) === "").join(", "),
    );
    return { status: "not_configured" };
  }

  // The item is built here, whole: the intake's ENVELOPE (`source`,
  // `source_item_id`, `payload`) around the five agreed fields. `payload.body`
  // and `payload.subject` are what the intake reads as the item's text (they
  // feed its risk scan), so they are written for a human reader and carry the
  // reply-to address; the structured fields ride alongside for the machine. No
  // request field beyond the validated name/reply-to/message ever reaches the
  // wire.
  const receivedAt = new Date().toISOString();
  const item = {
    source: INTAKE_SOURCE,
    // Unique per source — the intake's store dedupes on (source,
    // source_item_id), so this must be minted per submission, never reused.
    source_item_id: crypto.randomUUID(),
    payload: {
      subject: `Contact form — ${msg.name}`,
      body: `${msg.message}\n\n— ${msg.name} <${msg.replyTo}>`,
      name: msg.name,
      replyTo: msg.replyTo,
      message: msg.message,
      receivedAt: receivedAt,
      source: INTAKE_SOURCE,
    },
  };

  let upstream: Response;
  try {
    upstream = await fetch(intakeUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-inbound-secret": intakeSecret,
      },
      body: JSON.stringify(item),
      // Never follow a redirect: a followed 3xx replays this POST as an empty
      // GET, so the message is LOST while the landing page answers 200 — a
      // success report for a submission that was dropped. Manual mode surfaces
      // the 3xx so it takes the `failed` path below.
      redirect: "manual",
    });
  } catch (err) {
    console.error("contact: intake request failed:", err instanceof Error ? err.message : "network error");
    return { status: "failed" };
  }

  if (!upstream.ok) {
    // Log the intake status only — never the response body's contents.
    console.error(`contact: intake rejected the submission (${upstream.status})`);
    return { status: "failed" };
  }

  return { status: "enqueued" };
}
