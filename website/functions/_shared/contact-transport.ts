/**
 * The contact form's delivery TRANSPORT SEAM — issue #2409.
 *
 * ⚠️  PENDING OWNER DECISION — THE TRANSPORT IS **NOT SETTLED**.
 *
 * There is no owner ruling on which mail provider the website contact form
 * should deliver through. Nothing here may be read as that decision, and
 * nothing outside this file may name a provider: the point of this module is
 * that the choice is still OPEN.
 *
 * The body below implements one provider (Resend) as the PLACEHOLDER that
 * keeps `/api/contact` exercisable end-to-end today. It is deliberately the
 * ONLY place in the website codebase that knows a provider's name, credential
 * variable, endpoint or wire format. Swapping transport later is a change to
 * THIS FILE ONLY — `deliver()` is the sole entry point, and its payload/result
 * types are provider-neutral.
 *
 * Owner decisions that ARE settled, and stay settled here:
 *   - RECIPIENT: `hello@premiselabs.co` (owner ruling, issue #2409,
 *     2026-09-18). It arrives in `msg.to` from the caller — this module never
 *     chooses the address, and never reads one from the request.
 *   - FAIL LOUDLY: a missing credential is a visible failure
 *     (`status: "not_configured"` → the caller's 503), never a silent success.
 *
 * Environment names match the product's established convention, set by
 * `tortoise/email_notify.py` (the invite/OTP/onboarding sender): the same
 * `RESEND_API_KEY` + `RESEND_FROM_EMAIL` credential serves both surfaces, so
 * ops has one thing to bind. (`EMAIL_LINK_BASE_URL` is that file's link-host
 * variable — the contact form builds no links, so it has no use for it.) When
 * the transport is decided, the variable(s) it needs move to the new
 * provider's convention here and nowhere else.
 *
 * SECRET HANDLING: the key is read from `env` and used only as an
 * `Authorization` header. It is never logged, never echoed in a response, and
 * never placed in a URL — a bearer token in a URL lands in access logs.
 */

/** A message to deliver. Provider-neutral: the seam decides the wire format. */
export interface ContactMessage {
  /** Decided destination (`hello@premiselabs.co`). */
  to: string;
  /** The submitter's validated address — Reply-To, never the destination. */
  replyTo: string;
  /** Already control-character-stripped by the caller's validation. */
  name: string;
  message: string;
}

/** The Cloudflare Pages `env` object, passed through opaquely. */
export interface TransportEnv {
  [key: string]: unknown;
}

/**
 * The seam's whole result vocabulary. The caller maps each case to HTTP; it
 * never learns a provider status code, error body or credential name.
 */
export type DeliveryOutcome =
  | { status: "sent" }
  | { status: "not_configured" }
  | { status: "failed" };

// ── Current placeholder implementation (PENDING — see the header) ──────────
// Everything below this line is the swap surface. Replacing the provider means
// replacing `sendViaTransport()` (and the two env reads in `deliver()`); the
// `deliver()` signature and the outcome vocabulary stay as they are.

const TRANSPORT_URL = "https://api.resend.com/emails";
const TRANSPORT_KEY_ENV = "RESEND_API_KEY";
const TRANSPORT_FROM_ENV = "RESEND_FROM_EMAIL";
const FROM_DEFAULT = "noreply@premiselabs.co";

function envString(env: TransportEnv, name: string): string {
  const v = env[name];
  return typeof v === "string" ? v.trim() : "";
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** The provider call — the single place the transport's wire format lives. */
async function sendViaTransport(msg: ContactMessage, apiKey: string, from: string): Promise<Response> {
  const subject = `Website contact — ${msg.name.slice(0, 60)}`;
  const text =
    `New message from the website contact form.\n\n` +
    `Name:    ${msg.name}\n` +
    `Reply-to: ${msg.replyTo}\n\n` +
    `Message:\n${msg.message}\n`;
  const html =
    `<h2 style="font-family:system-ui,sans-serif;">New website contact message</h2>` +
    `<p style="font-family:system-ui,sans-serif;"><strong>Name:</strong> ${escapeHtml(msg.name)}<br>` +
    `<strong>Reply-to:</strong> ${escapeHtml(msg.replyTo)}</p>` +
    `<pre style="font-family:ui-monospace,monospace;white-space:pre-wrap;">${escapeHtml(msg.message)}</pre>` +
    `<p style="font-family:system-ui,sans-serif;color:#666;font-size:12px;">` +
    `Sent from the contact form at premiselabs.co. Reply directly to this email to answer ${escapeHtml(msg.name)}.</p>`;
  return fetch(TRANSPORT_URL, {
    method: "POST",
    headers: {
      // The key travels as a header, never in the URL (URLs are logged).
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from,
      to: [msg.to],
      reply_to: msg.replyTo,
      subject,
      text,
      html,
    }),
  });
}

/**
 * Deliver one contact message through the currently configured transport.
 *
 * The credential gate is HERE, and it precedes the send call: without it there
 * is no send-capable credential, and the outcome must be a visible
 * `not_configured` the caller turns into a 503 — never a success that drops the
 * message on the floor. (Pinning the ordering in-source is what the test suite
 * checks; the naming of the variable is operator-facing only — the visitor
 * message the caller builds never names it.)
 */
export async function deliver(msg: ContactMessage, env: TransportEnv): Promise<DeliveryOutcome> {
  const apiKey = envString(env, TRANSPORT_KEY_ENV);
  if (apiKey === "") {
    // Visible to an operator: the ABSENCE of the variable is the condition, and
    // naming it is what lets them fix it. No secret value is involved.
    console.error(`contact: ${TRANSPORT_KEY_ENV} is unset — contact form cannot deliver`);
    return { status: "not_configured" };
  }

  const from = envString(env, TRANSPORT_FROM_ENV) || FROM_DEFAULT;

  let upstream: Response;
  try {
    upstream = await sendViaTransport(msg, apiKey, from);
  } catch (err) {
    console.error("contact: transport request failed:", err instanceof Error ? err.message : "network error");
    return { status: "failed" };
  }

  if (!upstream.ok) {
    // Log the transport's status + message (never the key, never the body).
    const detail = await upstream.text().catch(() => "");
    console.error(`contact: transport rejected the send (${upstream.status}): ${detail.slice(0, 300)}`);
    return { status: "failed" };
  }

  return { status: "sent" };
}
