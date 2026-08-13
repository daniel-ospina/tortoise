// Test stub for https://esm.sh/standardwebhooks@1.0.0. Path 2 (auth-hook
// signature) only constructs Webhook when AUTH_HOOK_SECRET is set; the CORS
// harness leaves it unset so the function fails CLOSED (401) before reaching
// this stub. Throwing proves that.
export class Webhook {
  constructor(_secret: string) {}
  verify(): never {
    throw new Error("Webhook.verify called — unexpected on CORS-test paths");
  }
}
