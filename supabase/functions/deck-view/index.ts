// Deck-view edge function entrypoint.
//
// Thin wrapper: wires Deno.env into the pure handle() (which has zero imports so
// it can run under Node for tests). Deployed with verify_jwt=false (see
// supabase/config.toml) — the anonymous browser POST comes from the gated
// investors page.
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { handle } from "./handle.ts";

serve((req) => handle(req, Deno.env.toObject()));
