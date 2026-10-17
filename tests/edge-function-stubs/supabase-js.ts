// Test stub for https://esm.sh/@supabase/supabase-js@2. Both auth paths are
// exercised by the harness: Path 1 (user JWT) via a configurable getUser that
// returns a fixed user when set, Path 2 (auth hook) never calls the client.
// rpc() is configurable so the harness can drive the 502 (provision_team
// error) and 201 (success) response paths, and it RECORDS every (name, params)
// pair so a caller can assert the RPC ARGUMENTS an invariant depends on (the
// deterministic per-user org id, #2810).
let rpcResult: { error: unknown } = { error: new Error("default rpc failure") };
let jwtUser: { id: string; email: string; user_metadata?: Record<string, unknown> } | null = null;
const rpcCalls: { name: string; params: Record<string, unknown> | undefined }[] = [];

export function __setRpcResult(v: { error: unknown }): void {
  rpcResult = v;
}

export function __setJwtUser(
  u: { id: string; email: string; user_metadata?: Record<string, unknown> } | null
): void {
  jwtUser = u;
}

/** Every rpc() call made so far, oldest first; `params` is the argument object. */
export function __rpcCalls(): { name: string; params: Record<string, unknown> | undefined }[] {
  return rpcCalls;
}

/** Clear the rpc call log (a phase that asserts on the calls it makes). */
export function __resetRpcCalls(): void {
  rpcCalls.length = 0;
}

export function createClient(): {
  auth: {
    getUser: () => Promise<
      | { data: { user: { id: string; email: string; user_metadata?: Record<string, unknown> } }; error: null }
      | { data: { user: null }; error: Error }
    >
  };
  rpc: (name: string, params?: Record<string, unknown>) => Promise<{ error: unknown }>;
} {
  return {
    auth: {
      getUser: async () =>
        jwtUser
          ? { data: { user: jwtUser }, error: null }
          : { data: { user: null }, error: new Error("no JWT user set in stub") },
    },
    rpc: async (name: string, params?: Record<string, unknown>) => {
      if (name !== "provision_team") throw new Error(`unexpected rpc name: ${name}`);
      rpcCalls.push({ name, params });
      return rpcResult;
    },
  };
}
