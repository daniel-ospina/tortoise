#!/usr/bin/env node
/**
 * Provision-gateway mock for the /api/provision BFF suite.
 *
 * WHAT IT IS
 * ----------
 * A thin HTTP gateway in front of the shared `mock_supabase.mjs`:
 *
 *   POST /functions/v1/tenant-provision  -> handled HERE (emulated contract)
 *   /__gateway/*                          -> control/observability for tests
 *   everything else                       -> transparently proxied to the real
 *                                            mock (auth, PKCE, JWKS, /v1/*)
 *
 * WHY A GATEWAY RATHER THAN AN EDIT TO mock_supabase.mjs
 * -----------------------------------------------------
 * The BFF route under test must be reachable at `SUPABASE_URL/functions/v1/
 * tenant-provision`, but `SUPABASE_URL` also has to serve the REAL auth flow so
 * a genuine `__Host-session` cookie can be minted. Proxying keeps the auth path
 * exactly as real as every other auth suite (same mock, same ES256 signing,
 * same PKCE S256 check) while adding only the one upstream surface this route
 * talks to — and without modifying the shared mock that other suites depend on.
 *
 * The emulated contract is copied from the REAL Edge Function
 * (`supabase/functions/tenant-provision/index.ts`): POST only (405 otherwise),
 * a Bearer credential required (401), a body whose identity matches the caller
 * required (403), 201 `{org_id, org_name, api_key, graph_name}` on success, and
 * 500/502 on provisioning failure.
 */
import { createServer, request as httpRequest } from "node:http";

const PORT = Number(process.env.GATEWAY_PORT ?? 8793);
const UPSTREAM_HOST = "127.0.0.1";
const UPSTREAM_PORT = Number(process.env.MOCK_PORT ?? 8791);

const PROVISION_PATH = "/functions/v1/tenant-provision";

/**
 * The LAST provision request the gateway saw. This is the end-to-end proof that
 * the credential was attached SERVER-SIDE: the browser never sent an
 * Authorization header, so anything recorded here was minted by the BFF.
 */
let lastRequest = null;

/** Injected provision status. null = healthy; otherwise a status to return. */
const FAULTS = { provision: null }; // null | 4xx/5xx status number

const json = (res, status, body, extraHeaders = {}) => {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
    ...extraHeaders,
  });
  res.end(payload);
};

function readBody(req, cb) {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => cb(Buffer.concat(chunks)));
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);

  // ── Control/observability: what the provision endpoint actually received ──
  if (url.pathname === "/__gateway/seen") {
    // No body reading needed; this is a GET-style probe but we accept both.
    if (req.method === "POST") {
      readBody(req, (raw) => {
        let parsed = {};
        try { parsed = raw.length ? JSON.parse(raw.toString()) : {}; } catch { /* ignore */ }
        const seen = lastRequest;
        if (parsed.reset) lastRequest = null;
        json(res, 200, { seen });
      });
    } else {
      json(res, 200, { seen: lastRequest });
    }
    return;
  }

  if (url.pathname === "/__gateway/fault") {
    readBody(req, (raw) => {
      let parsed = {};
      try { parsed = JSON.parse(raw.toString()); } catch { /* ignore */ }
      const p = parsed.provision;
      if (p === null) FAULTS.provision = null;
      else if (Number.isInteger(p) && p >= 400 && p <= 599) FAULTS.provision = p;
      json(res, 200, { ok: true, faults: { ...FAULTS } });
    });
    return;
  }

  readBody(req, (buf) => {
    // ── Emulated tenant-provision Edge Function ────────────────────────────
    if (url.pathname === PROVISION_PATH) {
      // Record FIRST, so even a rejected call is observable.
      lastRequest = {
        method: req.method,
        authorization: req.headers.authorization ?? null,
        contentType: req.headers["content-type"] ?? null,
        body: buf.length ? buf.toString() : null,
        cookieLeaked: (req.headers.cookie ?? "").includes("__Host-session"),
        origin: req.headers.origin ?? null,
      };

      if (req.method !== "POST") {
        return json(res, 405, { error: "Method not allowed" });
      }
      const auth = req.headers.authorization ?? "";
      if (!auth.startsWith("Bearer ") || !auth.slice(7).trim()) {
        return json(res, 401, {
          error:
            "Unauthorized: expected a signed Supabase auth-hook request or " +
            "a user JWT matching the provisioning target",
        });
      }
      // Injected fault: a real status from the provision endpoint. Used to
      // prove the BFF maps failures honestly (and that an upstream 401 is not
      // forwarded as OUR 401 — the #3485 rule).
      if (FAULTS.provision !== null) {
        const faultBody =
          FAULTS.provision === 500 ? { error: "Internal server error" }
          : FAULTS.provision === 502 ? { error: "Provisioning failed. Please try again." }
          : { error: "injected_provision_fault" };
        return json(res, FAULTS.provision, faultBody);
      }

      let parsed;
      try {
        parsed = buf.length ? JSON.parse(buf.toString()) : {};
      } catch {
        return json(res, 400, { error: "invalid JSON body" });
      }
      const isObject = (v) => typeof v === "object" && v !== null;
      const parsedObj = isObject(parsed) ? parsed : null;
      const targetId = parsedObj
        ? (parsedObj.user_id || (isObject(parsedObj.user) ? parsedObj.user.id : ""))
        : "";
      if (!parsedObj || !targetId || typeof targetId !== "string") {
        // #802: the provisioning target must BE the authenticated caller.
        return json(res, 403, {
          error:
            "Forbidden: provisioning target does not match the " +
            "authenticated caller",
        });
      }
      return json(res, 201, {
        org_id: "0123456789abcdef01234567",
        org_name: parsedObj.org_name || "user",
        api_key: "tt_" + "b".repeat(64),
        graph_name: "org_0123456789abcdef01234567",
      });
    }

    // ── Transparent proxy to the real auth mock ────────────────────────────
    const proxyReq = httpRequest(
      {
        host: UPSTREAM_HOST,
        port: UPSTREAM_PORT,
        path: req.url,
        method: req.method,
        headers: req.headers,
      },
      (proxyRes) => {
        res.writeHead(proxyRes.statusCode, proxyRes.headers);
        proxyRes.pipe(res);
      },
    );
    proxyReq.on("error", () =>
      json(res, 502, { error: "gateway_upstream_unreachable" }),
    );
    if (buf.length) proxyReq.write(buf);
    proxyReq.end();
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`provision gateway listening on http://127.0.0.1:${PORT}\n`);
});
