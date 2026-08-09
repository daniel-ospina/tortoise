// Waitlist-subscribe edge function entrypoint (#373).
//
// Thin wrapper: wires Deno.env into the pure handle() (which has zero
// imports so it can run under Node for tests). Deployed with verify_jwt=false
// (see supabase/config.toml) — anonymous browser POSTs from premiselabs.co.
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { handle } from "./handle.ts";

serve((req) => handle(req, Deno.env.toObject()));
