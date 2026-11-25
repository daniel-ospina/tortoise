# #14607 · Why the Tortoise MCP is `core` and does not load

**Measured** on the `cmux` cell, 2026-11-25, at `feat/14607-agent-onboarding`
(worktree `.worktrees/14607-onboard`). Every fact below is a command's output, not
an inference — **and where a block is quoted from issue #14607's own report rather
than measured here, it says so**. The distinction matters on this box: it has
GitHub-only egress, so the hosted endpoint cannot be reached from it at all
(§"The cell-specific third fact"), and a block that shows the endpoint answering
is a block from somewhere else.

## 1. The symptom, as the agent sees it

```bash
$ mcp_catalog
tortoise [core, not-loaded]
```

(measured on this cell, 2026-11-25T04:28Z — the catalog is local state, so it
reproduces here.)

A `core` server — meant to be always-on — is down, and the only surface that says
so is a status string in a catalog listing. Nothing puts the failure into the
agent's turn.

**What `mcp_load` then returns depends on the box, and the difference is the whole
point of this document.** On a box that can reach the endpoint, it is an auth
rejection — this is issue #14607's own report, and the same `-32001` body is
recorded in `tortoise/mcp_bridge.py`'s #10605 post-mortem:

```text
Streamable HTTP error: Error POSTing to endpoint:
{"jsonrpc":"2.0","error":{"code":-32001,
  "message":"Unauthorized: invalid or missing Bearer token.
             Expected format: Authorization: Bearer tt_<key>/tk_<key>
             (or an OAuth access token for #524 OAuth clients)"}}
```

On **this** cell the same verb fails one layer earlier, at transport — measured
here, 2026-11-25T04:28Z:

```bash
$ mcp_load tortoise
MCP error -32603: MCP transport failure talking to https://api.premiselabs.co/mcp/:
  [Errno -3] Temporary failure in name resolution
```

One verb, two different classes, and an agent that reads either as "the key is
bad" is sent to the wrong layer. That is R2's defect stated as a measurement.

## 2. What the harness is actually configured with

The **tracked** harness config in this repo, as shipped **before this change**
(`.mcp.json` at the worktree root — the entry §5 installs replaces it):

```json
"tortoise": {
  "type": "http",
  "url": "https://api.premiselabs.co/mcp/",
  "headers": { "Authorization": "Bearer ${TORTOISE_API_KEY}" }
}
```

and, at user scope, `~/.pi/agent/.mcp.json` **declared** a **different** server
under the same name — past tense on purpose: **the installer has since rewritten
that file on this cell** (`tortoise onboard --harness pi --scope user`, run at
2026-11-25T01:51Z), so the object below is the *before* state, quoted ABRIDGED (the
real entry also carried `PYTHONPATH`, `purpose`, `whenToLoad` and `cost`). The
pre-rewrite file is kept alongside it as `~/.pi/agent/.mcp.json.bak-14607` — the
only rollback, so it is deliberately NOT deleted — and the file `~/.pi/agent/.mcp.json`
now holds the credential-free bridge. **Anyone quoting this block as the current
state is quoting the defect, not the box.**

```json
"tortoise": {
  "command": "${TORTOISE_HOME:-${HOME}/Documents/GitHub/tortoise}/.venv/bin/python3",
  "args": ["-m", "tortoise.mcp_server"],
  "cwd":   "${TORTOISE_HOME:-${HOME}/Documents/GitHub/tortoise}",
  "env": { "TORTOISE_DB_URI": "docker://:@127.0.0.1:16379/tortoise" }
}
```

Two entries, same name, **different transports**, and neither was verified — which
is the state the two defects below were diagnosed from.

## 3. The four independent things that are wrong, measured

| # | probe | result |
|---|---|---|
| 1 | `TORTOISE_API_KEY` | **set** — `len 35`, prefix `tt_`. (An earlier revision of this file recorded this as *unset* and pasted a `tortoise config show` block saying `not set` / `Identity: none resolved`. **That was wrong, and it is corrected here**: the variable is present in this cell's ambient environment — measured in this shell and in the long-lived controller process that started the previous day — so the §4 conclusion it supported is restated below.) |
| 2 | `~/.pi/agent/tortoise-config.json` (the harness credential store) | **absent** |
| 3 | `~/.tortoise/credentials.json`, `./.tortoise` (the CLI stores) | **absent, absent** |
| 4 | `$TORTOISE_HOME` (the user-scope stdio entry's root) | **unset** → defaults to `$HOME/Documents/GitHub/tortoise`, which **does not exist** on this box |

`tortoise config show` — the product's own credential resolver — agrees, and it
agrees with row 1, not with the revision it replaced:

```bash
  TORTOISE_API_KEY (env)                                 set        ←  USED
  ./.tortoise                                            absent
  ~/.tortoise/credentials.json                           absent

Identity
  api_url       https://api.premiselabs.co   (masked)
  api_key       tt_…d14a  (sha256:6c404ea36be4)
```

## 4. Root cause — TWO defects, one shape

### R1 · The harness config is a **CAPTURE**, not a lookup

`"Authorization": "Bearer ${TORTOISE_API_KEY}"` is an expansion of an environment
variable performed **once, at the harness's process start** — the value is frozen
into the request from then on, and the config can never say whether it was
resolved at all. Two consequences, and only one of them is observable on this
box:

- **The empty-bearer shape (documented, not measured here).** With the variable
  unset the expansion is the empty string, so the request goes out carrying an
  `Authorization: Bearer` header and an EMPTY token — and `tortoise/mcp_auth.py`
  can only answer that one way (`OrgResolutionMiddleware.dispatch`, `:1547`):
  `-32001 Unauthorized: invalid or missing Bearer token`. **The message names the
  credential's VALUE; the fault is that the client never had one.** This is the
  shape #14607 reports, and it is the class `tortoise/mcp_bridge.py`'s docstring
  already documents as #10605's post-mortem.
  ⛔ **It is NOT what this box measured.** Here `TORTOISE_API_KEY` is **set**
  (§3 row 1), so the shipped entry sends a REAL bearer, and this cell's own
  failure is transport (§"the cell-specific third fact") — an earlier revision of
  this document claimed the unset case as measured here, which was false.
- **The captured-value shape (structural).** Whatever the variable held when the
  harness started is what it keeps sending — so re-minting or rotating the key
  changes nothing until the harness restarts, and no surface can report that the
  value it holds is stale.

This class is **already documented and already fixed** in
`tortoise/mcp_bridge.py` (its docstring is #10605's post-mortem) — but the fix was
never *applied to the config*, so the defect is live in the tracked `.mcp.json`
and in the user-scope file. The bridge and the config disagree, and nothing
notices.

⚠️ **What the config change is therefore worth — stated plainly.** It removes a
defect *class* and makes the credential path resolvable per request and
verifiable; it is **not** shown here to fix this cell's outage, which is
transport. The one command still owed a box with egress is `tortoise mcp-status`
(expect `OK`) after `tortoise onboard --harness pi`.

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
