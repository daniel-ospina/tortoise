---
title: "Capture consent — separating the MCP Bearer credential from session-capture authorization (#3615)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
created: 2026-09-16
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-capture-consent
issue: 3615
tags: [capture, consent, privacy, hooks, mcp, telemetry]
---

# Capture consent research — #3615 (Phase 1.5 artifact)

Persisted so the scoping decision is auditable (problem-verify gate: "no research artifact on
disk"). Scope: the mechanism for an explicit, credential-independent capture opt-in, and how to
migrate a silently-enabled data-sharing default without either silence or alert fatigue.

## Axis 1 — Consent/opt-in design for telemetry & data-sharing (rating: high)

- **canonical** — GDPR Art. 25 "data protection by default" requires the most privacy-protective
  defaults and processing only what is necessary *without the data subject's intervention*;
  pre-ticked boxes / inactivity are not valid consent — EDPB Guidelines 4/2019
  (https://www.edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_201904_dataprotection_by_design_and_by_default_v2.0_en.pdf),
  legislation.gov.uk Art. 25 (https://www.legislation.gov.uk/eur/2016/679/article/25).
- **canonical** — the auth/consent boundary is a first-class separation in OAuth: authentication
  yields a token; consent is a *separate, granular* scope approval. Scopes are "the unit of consent
  and policy boundary"; possessing a credential is not consent —
  https://oauth.net/2/scope/, https://nhimg.org/articles/scopes-and-claims-in-oauth-and-oidc-where-each-belongs/
- **competitor-precedent** — Teleport anonymous telemetry is strictly opt-in
  (`TELEPORT_ANONYMOUS_TELEMETRY=1`; unset ⇒ nothing collected) —
  https://goteleport.com/docs/ver/17.x/reference/machine-id/telemetry.md ; terraform-ls telemetry is
  off by default, enabled only by an explicit client capability —
  https://github.com/hashicorp/terraform-ls/blob/main/docs/telemetry.md ; OpenTelemetry classifies
  "opt-in" attributes as not included by default —
  https://opentelemetry.io/docs/specs/semconv/general/attribute-requirement-level/
- **pitfalls** — bundled consent is invalid: consent must be a clear affirmative act, separate and
  granular, and if refusing it denies the service it is not "freely given". That is exactly this
  defect (refusing capture denies hosted MCP) — EDPB Guidelines 3/2022 on dark patterns
  (https://www.edpb.europa.eu/system/files/2022-03/edpb_03-2022_guidelines_on_dark_patterns_in_social_media_business_pages_en.pdf)

## Axis 2 — Opt-in mechanics readable by a session-end hook (rating: medium)

- **canonical** — `DO_NOT_TRACK` is the de-facto cross-tool convention, but it is an *opt-out*
  grammar; opt-in tooling uses product-specific variables (`HOMEBREW_NO_ANALYTICS`,
  `DOTNET_CLI_TELEMETRY_OPTOUT`) — https://donottrack.sh/ ,
  https://learn.microsoft.com/en-us/dotnet/core/tools/telemetry
- **competitor-precedent** — Homebrew notifies the user *before* analytics are enabled and exposes
  two surfaces: a durable file/state command (`brew analytics off`) and a per-run env override
  (`HOMEBREW_NO_ANALYTICS=1`) — the file is the persistent source of truth —
  https://docs.brew.sh/Analytics
- **canonical (precedence)** — recommended precedence is CLI flag > env var > config file > default,
  with the config path documented; env vars are inherited by child processes and can leak into logs
  and errors — https://deepwiki.com/cli-guidelines/cli-guidelines/11-configuration-and-environment
- **pitfalls** — an env-only gate is fragile: Homebrew 4.1.0 turned `HOMEBREW_NO_ANALYTICS` into a
  silent no-op (https://brew.sh/2023/07/20/homebrew-4.1.0/), and env vars leak into subprocesses
  (https://softwareengineering.stackexchange.com/questions/148042/).
- **pitfalls** — an undocumented / divergent config path produces machine-global vs per-repo scope
  confusion (https://codyaray.com/2020/07/cli-design-best-practices).

## Axis 3 — Breaking-change migration for silently-enabled data-sharing (rating: medium)

- **canonical** — a mature policy announces the change in a release and allows a support window
  before removal, with runtime warnings and a migration-guide link —
  https://docs.dapr.io/operations/support/breaking-changes-and-deprecations/
- **competitor-precedent** — Kubernetes requires deprecated CLI to emit warnings while guaranteeing
  a minimum support window (https://kubernetes.io/docs/reference/deprecation-policy/);
  Salesforce CLI shows warnings for ≥4 months and documents the deprecation in `--help`.
- **pitfalls (silent vanish)** — AMD's quiet removal of transparent memory encryption left users
  unaware a security feature had vanished; UX guidance argues silent removal is itself the defect —
  disable *visibly* rather than let a capability disappear
  (https://www.tomshardware.com/pc-components/cpus/amd-silently-removes-memory-encryption-from-consumer-ryzen-cpus)
- **pitfalls (alert fatigue)** — repeated per-invocation warnings are a documented frustration;
  guidance is a finite, quiet window (Homebrew's always-shown notice was filed as a bug —
  https://github.com/Homebrew/brew/issues/15678).

## Implication for #3615

Auth and consent must be separated (Axis 1): the Bearer credential must stop being consulted as a
capture signal. Because the hook fires at process exit with no prompt (Axis 2), the decision must be
a cheap, explicit, default-OFF predicate — env is the minimal surface that works uniformly for
env-keyed and file-keyed machines, matching the in-repo `TORTOISE_INDEX_CHILD_STDERR` /
`TORTOISE_ALLOW_EMBEDDED` precedent. The known env weakness (leakage, silent no-op) is mitigated by:
explicit naming, default OFF, a one-time (not per-session) migration notice carrying the exact
re-enable line, and docs (Axis 3). The dashboard's `session_recording` model was evaluated and
rejected as the gate: it is a default-ON *opt-out* switch (#1927), i.e. the opposite polarity — it
cannot express "off unless explicitly enabled".

The two layers are deliberate and must not be conflated:

| Layer | Scope | Default | Owner |
|---|---|---|---|
| Server recording policy (`session_recording`) | per-organization | ON (ToS-covered) + quiet 409 off-switch (#1927) | UNCHANGED by #3615 |
| Client transmission authorization (`TORTOISE_CAPTURE`) | per-host | OFF — explicit opt-in | NEW in #3615 |

The predicate is deliberately **host-agnostic**: a self-hosted daemon is still
data leaving the machine, so the requirement does not depend on the endpoint
the resolved config names.

## Rejected alternatives (#3615 solution-diverge) and why

- **B1 — a `capture` key in the existing credential file** (`./.tortoise` /
  `~/.tortoise/credentials.json`). Rejected: `_resolve_config_path` returns
  EARLY for a non-empty `TORTOISE_API_KEY`, so the documented env-keyed setup
  never opens the file — the consent key would be invisible on exactly the
  machines the MCP recipe creates. It also puts consent inside the credential
  file (mixing the two concepts #3615 separates), and a `cwd/.tortoise` variant
  would let a repository opt a user in (the class #3660 files).
  *(A variant reading BOTH — env first, file as fallback — was considered and
  rejected too: it keeps the credential-file mixing and the repo-opt-in class,
  so it buys durability at the cost of both properties above.)*
- **B2 — a new dedicated config file + `tortoise capture enable|disable`.**
  Rejected as the larger change: a third artifact under `~/.tortoise/`, a new
  CLI lifecycle, and a divergent config-path surface — the cli-guidelines
  pitfall quoted in Axis 2. It remains the better shape if consent later needs
  a durable, auditable record; env is the minimal predicate for a hook that
  fires at process exit with no prompt.
- **B3 — reuse the pi extension's `~/.pi/agent/tortoise-config.json` `cloud`
  flag.** Rejected: cross-application coupling — the Python CLI does not read
  (and should not own) an agent-infra/pi config path, and a Claude Code user
  without pi would have to create a pi config file to opt in.
- **C — reuse the dashboard's per-graph `session_recording` setting.**
  Rejected on evidence: default-ON (ToS-covered) and documented as an optional
  off-switch, "not a consent gate" (#1927), so it cannot express "off unless
  explicitly enabled"; and a server gate decides AFTER the transcript has
  already been transmitted.
- **D — a per-session in-conversation consent token.** Rejected: highest
  consent fidelity, but it breaks unattended/headless capture entirely, keys on
  a session id the hook does not always have, and the agent can mint the token
  (self-consent), so the guarantee is weaker than it looks.
- **Hybrid file-default + env-override (the Homebrew shape)** and a **CLI
  `--consent` flag** for the manual primitives were both considered and
  rejected: the first needs the new config surface B2 sized and above; the
  second gives the AMBIENT path a per-run exemption, which is the bypass class
  the design exists to close.

## Accepted residual (recorded, not silently fixed)

An env-only opt-in is influenced by anything in the launch chain — a
repo-scoped env manager (`.envrc`, `mise.toml`) could export
`TORTOISE_CAPTURE=1`. Accepted: the value is still an explicit, *specifically
named* consent signal rather than an inference from the credential; the code it
authorises (a repo's own hooks) already runs with the user's privileges; and
Axis 2's durable-file recommendation is deferred to B2 should consent ever need
an auditable record. The related, more severe repo-controlled-host class is
filed separately (#3660).

Two caveats are recorded with it rather than left implicit:

- **Durability direction.** Env is not persistent. A `TORTOISE_CAPTURE=1`
  exported only in an interactive shell profile will not reach a GUI-launched
  harness, so such a user believes they opted in and silently gets no capture.
  This is fail-CLOSED (no privacy loss, a usability gap), and it is the mirror
  of the leak the residual above bounds. Axis 2's durable-file conclusion is
  exactly why B2 remains the better shape; env is the minimal predicate for a
  hook that fires at process exit with no prompt.
- **No retro-consent.** Sessions that were *not* captured because consent was
  absent are not queued, retried, or backfilled when consent is later granted.
  Consent authorizes future transmissions only.

## Migration DELIVERY (solution-verify cycle 2 P1)

Writing the notice to `~/.tortoise/capture-consent-notice` is **necessary but
not sufficient**, and the first cut mistook the one for the other. The
population this change targets runs a *stale copied* hook whose invocation is
`tortoise session capture … 2>/dev/null`: the refusal is correct but its message
is discarded, and the affected user has no reason to ever run `tortoise doctor`
— the only consumer of the file. The observable experience was therefore
"capture quietly stopped", which Axis 3 (the AMD precedent) names as the defect:
a security-affecting change must be disabled **visibly**.

A second, sharper defect the gate reproduced live: the visible notice was gated
on the marker's *absence*. Because the stale path's CLI writes that same marker,
the updated hook could then never speak — the notice was suppressed on precisely
the hosts it exists for.

Delivery is therefore two channels with different lifetimes:

| Channel | Written by | Lifetime |
|---|---|---|
| `~/.tortoise/capture-consent-notice` (content = the re-enable line) | the hook (first refusal) and both CLI primitives | once per machine — durable, discoverable, never rewritten |
| the visible stderr line | the hook on **every** session close while capture is off and a legacy credential is present | per invocation — cannot be suppressed by the marker |
| the next interactive command | `__main__._flush_pending_capture_notice`, gated on `sys.stderr.isatty()` (a redirected/piped stderr consumes nothing; a pty-allocating non-human caller is a declared, notice-only residual) | once per human, stamped separately in `…-notice.shown` |

The third row is the one that actually reaches the stale-hook population: a
machine/hook-facing command may not consume the human's single sighting, so the
next command a person really runs delivers it. `tortoise doctor` reports the
state as an informational row (`ℹ️`, not `⚠️` — capture-off is the default a
healthy install is *supposed* to be in, and painting it as a warning is the
alert-fatigue pattern this migration exists to avoid).

## Enforcement-layer note (gate finding)

The root defect ("credential presence ⇒ authorized to transmit") is a property of the *in-repo
client paths*. The server's shared `_capture_session_impl` (`tortoise/hosted_api.py`) is gated by
`session_recording`, deliberately default-ON (#1927 — a server change is out of #3615's scope), and
agent-infra's `reflect-hook.ts` is a separate repo (companion issue
daniel-ospina/agent-infra#1117). The in-repo
MCP tool `tortoise_session_capture` (and a hosted-backend `TortoiseSDK.capture_session`) routes
through that same server primitive and is likewise declared out of the client gate with its
rationale recorded in the table above (companion issue #3662). The same carve-out covers the SDK's
sibling `commit_session` → `POST /v1/sessions/commit` path (`tortoise/sdk.py::_post_commit`), which
also selects its credential from `api_key or $TORTOISE_API_KEY`: it is an explicit SDK call rather
than an automatic client path, and its payload is derived (summary/points/entities), not a raw
transcript — but it is named here so the enumeration is complete for the class "a credential must
not authorize transmission." #3615 therefore fixes the in-repo
**automatic client paths** and declares every cross-layer dependency rather than claiming
end-to-end closure.
