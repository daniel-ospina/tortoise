// Test stub for https://esm.sh/standardwebhooks@1.0.0 — implements REAL
// Standard-Webhooks signature verification (HMAC-SHA256 over
// `${webhook-id}.${webhook-timestamp}.${rawBody}`, base64, `v1,` prefix) so
// the runtime harness can exercise the authenticated hook path (Path 2) and
// reach the 400/500/502/201 responses. Only constructible when the harness
// sets AUTH_HOOK_SECRET; the no-secret 401 path never reaches this stub.
import { createHmac, timingSafeEqual } from "node:crypto";

export class Webhook {
  private secret: string;
  constructor(secret: string) {
    this.secret = secret;
  }
  verify(rawBody: string, headers: Record<string, string>): unknown {
    const id = headers["webhook-id"];
    const ts = headers["webhook-timestamp"];
    const sig = headers["webhook-signature"];
    if (!id || !ts || !sig || !sig.startsWith("v1,")) {
      throw new Error("missing webhook headers");
    }
    const expected = createHmac("sha256", this.secret)
      .update(`${id}.${ts}.${rawBody}`)
      .digest();
    const provided = Buffer.from(sig.slice(3), "base64");
    if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
      throw new Error("signature mismatch");
    }
    return JSON.parse(rawBody);
  }
}
