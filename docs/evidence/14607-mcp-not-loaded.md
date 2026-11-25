# #14607 · Why the Tortoise MCP is `core` and does not load

**Measured** on the `cmux` cell, 2026-11-25, at `feat/14607-agent-onboarding`
(worktree `.worktrees/14607-onboard`). Every fact below is a command's output, not
an inference.

## 1. The symptom, as the agent sees it

```
$ mcp_catalog
tortoise [core, not-loaded]

$ mcp_load tortoise
Streamable HTTP error: Error POSTing to endpoint:
{"jsonrpc":"2.0","error":{"code":-32001,
  "message":"Unauthorized: invalid or missing Bearer token.
             Expected format: Authorization: Bearer tt_<key>/tk_<key>
             (or an OAuth access token for #524 OAuth clients)"}}
```

A `core` server — meant to be always-on — is down, and the only surface that says
so is a status string in a catalog listing. Nothing puts the failure into the
agent's turn.

## 2. What the harness is actually configured with

The **tracked** harness config in this repo (`.mcp.json` at the worktree root):

```json
"tortoise": {
  "type": "http",
  "url": "https://api.premiselabs.co/mcp/",
  "headers": { "Authorization": "Bearer ${TORTOISE_API_KEY}" }
}
```

and, at user scope, `~/.pi/agent/.mcp.json` declares a **different** server under
the same name:

```json
"tortoise": {
  "command": "${TORTOISE_HOME:-${HOME}/Documents/GitHub/tortoise}/.venv/bin/python3",
  "args": ["-m", "tortoise.mcp_server"],
  "cwd":   "${TORTOISE_HOME:-${HOME}/Documents/GitHub/tortoise}",
  "env": { "TORTOISE_DB_URI": "docker://:@127.0.0.1:16379/tortoise" }
}
```

Two entries, same name, **different transports**, and neither is verified.

## 3. The four independent things that are wrong, measured

| # | probe | result |
|---|---|---|
| 1 | `TORTOISE_API_KEY` | **unset** |
| 2 | `~/.pi/agent/tortoise-config.json` (the harness credential store) | **absent** |
| 3 | `~/.tortoise/credentials.json`, `./.tortoise` (the CLI stores) | **absent, absent** |
| 4 | `$TORTOISE_HOME` (the user-scope stdio entry's root) | **unset** → defaults to `$HOME/Documents/GitHub/tortoise`, which **does not exist** on this box |

`tortoise config show` — the product's own credential resolver — agrees:

```
  TORTOISE_API_KEY (env)                      not set
  ./.tortoise                                 absent
  ~/.tortoise/credentials.json                absent
Identity: none resolved.
```

## 4. Root cause — TWO defects, one shape

### R1 · The harness config is a **CAPTURE**, not a lookup

`"Authorization": "Bearer ${TORTOISE_API_KEY}"` is an expansion of an environment
variable that the harness read **once, at process start**. With the variable
unset, the expansion is the empty string, so the request goes out as:

```
Authorization: Bearer 
```

`tortoise/mcp_auth.py` can only answer that one way
(`OrgResolutionMiddleware.dispatch`, `:1547`): `-32001 Unauthorized: invalid or
missing Bearer token`. **The message names the credential's VALUE. The fault is
that the client never had one, and the config cannot say so.** A message that
points at the wrong layer is why this survived: every reader chased a key instead
of the capture.

This class is **already documented and already fixed** in
`tortoise/mcp_bridge.py` (its docstring is #10605's post-mortem) — but the fix was
never *applied to the config*, so the defect is live in the tracked `.mcp.json`
and in the user-scope file. The bridge and the config disagree, and nothing
notices.

### R2 · Nothing verifies the harness config

- `tortoise doctor` probes the **hosted identity** and the **graph** — it never
  reads the file the harness actually loads.
- `mcp_catalog` reports `not-loaded` and **no reason**.
- There is no verb whose job is "is the MCP reachable *from this agent*, and if
  not, why".

So a `core` server can be down for four structurally different reasons —
unset `${VAR}`, missing spawn path, no credential in any store, unreachable
endpoint — and all four are reported to the agent as the same opaque string.
**The absence of a verifier is what makes the failure silent.**

### The cell-specific third fact (not a defect, but it must not be confused with one)

On this box `api.premiselabs.co` **does not resolve** (the cell has GitHub-only
egress: `api.github.com` → 200, `api.premiselabs.co` → `Could not resolve host`).
That is a *transport* failure, categorically different from an auth rejection, and
a diagnostic that reports it as "invalid token" would send the operator to the
wrong layer a second time. Any verifier MUST separate them.

## 5. The fix

1. **Config → the bridge.** The harness entry becomes
   `python3 -m tortoise.mcp_bridge` with `TORTOISE_MCP_URL` and **no credential
   in the file**. The bridge resolves the key **per request** (env →
   `./.tortoise` → `~/.tortoise/credentials.json`), never sends a blank header,
   and answers a missing credential with the named `MCP-KEY-EMPTY` refusal that
   lists the sources it read. Nothing in the config can go stale.
2. **A verifier with named classes** — `tortoise mcp-status` (also
   `tortoise onboard --harness … --verify`): reads the config the harness reads,
   classifies the failure as one of a closed set, prints **one line**, and exits
   non-zero. The class names the layer, so the reader is never sent to the wrong
   one.
3. **The installer proves it** — `tortoise onboard --harness pi` writes the entry,
   makes a **live call**, and refuses to print a green verdict on an unverified
   install.

The recorded loud-failure demonstration is in §6 of the PR body / the test
`tests/test_mcp_selfcheck.py::test_broken_token_is_a_named_loud_failure`.
