// Test stub for https://esm.sh/@supabase/supabase-js@2. The JWT auth path
// (Path 1) is not exercised by the harness (hook mode only), so getUser
// always fails closed. rpc() is configurable so the harness can drive the
// 502 (provision_team error) and 201 (success) response paths.
let rpcResult: { error: unknown } = { error: new Error("default rpc failure") };

export function __setRpcResult(v: { error: unknown }): void {
  rpcResult = v;
}

export function createClient(): {
  auth: { getUser: () => Promise<{ data: { user: null }; error: Error }> };
  rpc: (name: string) => Promise<{ error: unknown }>;
} {
  return {
    auth: {
      getUser: async () => ({
        data: { user: null },
        error: new Error("JWT path not exercised by the CORS harness (hook mode)"),
      }),
    },
    rpc: async (name: string) => {
      if (name !== "provision_team") throw new Error(`unexpected rpc name: ${name}`);
      return rpcResult;
    },
  };
}
