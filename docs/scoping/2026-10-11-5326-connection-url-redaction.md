---
title: "Scoping #5326 — a password-bearing connection URL is redacted (the closed scheme allow-list + URL-grammar confirmation)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted
aboutObjects: capture, redaction, secret-hygiene
created: 2026-10-11
---

# Scoping — #5326: a password-bearing connection URL is not redacted

**Lane:** tm-5326 · **Branch:** `feat/5326-tm` · **Tier:** standard · **Domain:**
Complicated (an established pattern — a regex rule in an existing table, not a new
mechanism) · **Filed from:** the #4911 lane (cycle-4 security review), labelled
`P0-blocker`.

## Problem diamond

### The framing the issue hands over, and the one thing it gets wrong

\#5326 defines the problem as "no anchor is both precise and complete", and it is
right about the mechanism: a connection URL has **no vendor prefix**, so the
module's `anchored shapes only` rule cannot pin it. It then names two candidate
anchors — `(a)` a loose structural prefilter plus verification, `(b)` a closed
scheme allow-list plus a same-line `PGPASSWORD=`/`DATABASE_URL=` context token —
and says either is a design decision belonging in its own change. This is that
change.

The one thing the issue gets wrong is a **citation**, and it matters because the
design leans on comparables:

> "the shapes that work in practice (gitleaks' `database-url`-style rules,
> trufflehog's URI detectors)"

**There is no `database-url` rule in gitleaks.** Its default ruleset is 222 rules
and carries no URL-with-credentials rule at all: the only URL-family entries are
vendor-anchored or context-anchored (`slack-webhook-url`, `sidekiq-sensitive-url`,
`curl-auth-user`, `curl-auth-header`), and its generic mechanism is
keyword + assignment-operator + value-shape. So half the cited precedent does not
exist — and the half that does (trufflehog) is *stronger* than the issue claims,
because it is one design repeated three times:

| trufflehog detector | its rule | the verification it pairs with |
|---|---|---|
| `uri` (http/https) | `\bhttps?://[…]{0,50}:([…]{3,50})@[a-zA-Z0-9.-]+(?:\.[a-zA-Z]{2,})?(?::\d{1,5})?[\w/]+\b` | `url.Parse` + require `User.Password()`; skip passwords that are all `*`; skip known test hosts (`httpbin.org`, `httpwatch.com`) |
| `redis` | `\bredi[s]{1,2}://[\S]{3,50}:([\S]{3,50})@[-.%\w/:]+\b` | `url.Parse` + require `User.Password()`; skip `*`-only; skip `$`-prefixed (a shell variable) |
| `postgres` | `\b(?i)(postgres(?:ql)?)://\S+\b` | `pq.ParseURI` into a parameter map; ignore loopback by default (`detectLoopback = false`) |

Read together, the convergent design is the one the issue guessed at — **a closed
scheme anchor, a loose structural prefilter, then a structural confirmation** — and
it also settles two questions the issue left open:

* a **bounded** user/password class is what a mature scanner ships (trufflehog's
  `{3,50}`), so the earlier revision-3 objection over the bound is a recall
  trade to be *documented*, not a reason to ship a greedy class;
* an already-redacted password (`***`) and an unexpanded variable (`$VAR`) are
  **not** credentials — a refusal class the issue did not mention.

### Root cause

Not "the rule is missing". The rule was **built, measured three ways, and
removed**, and the removal is recorded in three places
(`tortoise/security.py`, `tests/test_capture_secret_redaction_4911.py::test_anchored_shapes_do_not_redact_prose`,
`docs/scoping/2026-09-25-4911-capture-secret-redaction.md`). The root cause is
that each revision was asked to solve BOTH precision and recall with ONE
mechanism — a bracket-class budget — and no such class exists, because the
discriminating information (is this a real credential field, or a documentation
example?) is not in the byte class.

### Contradiction test (run FIRST, per `AGENTS.md`)

| Candidate recorded decision | Does it govern this? |
|---|---|
| **#4911's pinned removal** of the `connection_url` rule (`test_anchored_shapes_do_not_redact_prose`, `tortoise/security.py`) | **Governs — and #5326 is its sanctioned reopen.** #4911's own residual list says the gap is "filed as its own issue"; #5326 *is* that issue and is labelled `P0-blocker`. The pinned test says in its own comment that turning it red is "a deliberate change to that decision, not a surprise". This change makes that deliberate change and moves the two URLs to a positive test. |
| **#3875 (SDK/MCP surface approval)** | No surface change: no tool, no public SDK method. `redact_secrets`' signature is unchanged. |
| **#4897** (the 5,000-char silent truncation on this same path) | Adjacent, and it sets the *shape*: the marker is visible and the removal is never silent. Honoured — only the password span is replaced, and the rest of the URL is byte-identical. |
| **The local-spool ruling** (#4911's `capture_spool` exclusion) | Unaffected: the spool is still raw by decision, and this rule sits on the same write chokepoint its replay passes through. |

## Solution diamond

### Divergence

| Option | What it would be | Verdict |
|---|---|---|
| **A. Scheme-anchored prefilter + URL-grammar confirmation** (this change) | a closed scheme allow-list, a required `user:password@host` userinfo, a host-SHAPED host, a structural placeholder refusal | **Chosen** — the convergent trufflehog design, and the only option that is complete on real shapes while keeping the FP class smaller than revision 1's |
| **B. A same-line context token** (`DATABASE_URL=`) as the precision lever | the issue's option (b) | **Rejected as the primary lever.** It is a context-anchored `key=…` heuristic, which the module bans; and it is *unmeasurable* here — the 167-match corpus is this repo's own dev URI quoted as `TORTOISE_DB_URI='docker://…'`, so whether a context token precedes it cannot be decided without the corpus, and a precision claim resting on an unverified corpus property is the failure mode the issue itself warns about ("a leak that looks like coverage"). The *scheme allow-list* half of (b) is adopted. |
| **C. Loose prefilter + a live connection attempt** | the issue's option (a), literally | **Rejected.** This scrubber runs on the capture write path (session-end, client-controlled text, ~3 s/MB measured budget). A network dial per candidate is a latency and availability dependency on a security control, and it *fails open* offline. Structural confirmation is the same design with no dependency. |
| **D. A loopback-host exemption** (spare `localhost`) | trufflehog's `detectLoopback = false` default; would spare all 167 of the repo's own dev-URI matches | **Rejected.** #5326's own third example is `redis://default:mypassword@localhost:6379`. A loopback exemption leaves the issue's example unredacted — a rule that does not fix the filed defect. Recorded in the test that pins the decision. |
| **E. Require a non-empty username** | spares the dev URI (`docker://:falkordb@…` has an empty user) and matches all three of the issue's examples | **Rejected — it is a P0 leak.** `docker://` is tortoise's OWN `TORTOISE_DB_URI` scheme and its documented production form is `docker://:password@host:6379/graph` (see `tools/ask_shape_rate.py`, which normalizes exactly that shape). Excluding it leaves the product's own primary credential form in cleartext with `capture_redactions: 0`. |
| **F. A placeholder-WORD list** (`changeme`, `hunter2`, `falkordb`) | spare documented example passwords | **Rejected.** A value dictionary is the entropy heuristic the module refuses, in disguise, with an unbounded tail. Structural placeholders only (`***`, `%2A`, `$VAR`, `<…>`, our own marker). |

### Convergence — the design, and why each part is load-bearing

The rule lives in `tortoise/security.py` (where #4911 put the table) as the
`connection_url` kind, and it is the only rule in the table whose marker is not
the whole match: it replaces the **password span** of
`scheme://user:password@host`, so the stored record keeps its engine, user, host,
port, path, query and fragment and stays diagnostic.

* **Anchor = the scheme NAME.** `_CONNECTION_URL_SCHEMES` is a closed frozenset
  (~40 schemes whose URL form conventionally carries a password in the userinfo:
  engines, brokers, `docker` (Tortoise's own), the `http(s)`/`ws(s)` family for
  basic auth and provider DSNs like `SENTRY_DSN`, and the transfer/directory/mail
  protocols). The pattern's alternation is **built from the set**, longest-first,
  so the two cannot drift — pinned by
  `test_every_scheme_in_the_allow_list_is_actually_matched`. This is a literal
  NAME anchor, word for word the family the module already documents for
  `aws_secret_access_key` and the `bearer_token` pair; the name happens to be a
  scheme instead of a parameter. A CUSTOM scheme is therefore not covered — the
  closed list's inherent property, recorded rather than implied.
* **Confirmation = the URL grammar.** A `:`-separated password in the userinfo
  (so a username-only URL is not a credential), a host-SHAPED host (a registered
  name of any label count, an IPv4 literal, a bracketed IPv6 literal) — and
  deliberately **no dotted-host gate** (revision 2's leak: it dropped `@dbserver`,
  `@cache` and every IP literal).
* **The class that "swallowed surrounding text" is fixed by construction.**
  The password class is **lazy**, stops at the structured-text delimiters, and
  the host is host-shaped, so minified JSON round a URL keeps every key and
  address: `{"url":"postgres://user:pw@host","owner":"ops@example.com"}` redacts
  `pw` and nothing else. Pinned from **both** sides — a greedy twin is built in
  the test and asserted to destroy the neighbouring keys, so the laziness cannot
  be "tidied" away — and the other direction too: a LAX host boundary lets the
  host class backtrack one character, the match settles on the FIRST `@`, and
  the tail of a password containing `@` (`postgres://user:pw@abcd-@x`) survives
  while the count reports a redaction. The shipped boundary therefore demands
  **two** things: a **maximal** host AND that the host is the URL's own
  authority — **no further `@` reachable** through password-legal characters
  from the host end. The second assertion closes `postgres://user:p@ss:123@host`,
  where the tail `ss:123` is host-shaped and `:` is neither a host-body
  character nor a password stop, so maximality alone settled on the first `@`
  and left the password tail behind with a redaction counted (#5470). The scan
  is bounded at 512, the password's own bound, which keeps it constant-cost.
* **Refusals are in the PATTERN, not in a callable — and that is a correctness
  requirement, not a style choice.** `re.subn` counts MATCHES, so a replacement
  function that returns a candidate unchanged is still counted: it would inflate
  `capture_redactions` on the capture path's second pass (#4194) while the text
  did not change. Refusing at the pattern level leaves nothing to count.
* **Order: LAST in the table.** It is the broadest anchor, so a credential used
  AS a password (`postgres://user:glpat_…@host`, `…:eyJ…@host`) must keep its own
  label — the specific rule claims the span first and this rule's placeholder gate
  then declines its marker. This was measured, not assumed: with the rule placed
  before `jwt`, the JWT case came back `{'connection_url': 1}` and `jwt` was
  absent.
* **A refusal must always be explained.** The `P0` question for a *bounded* class
  is not "does it miss things" but "does it leave a PREFIX with a redaction
  reported" (#5470's false-assurance class). Pinned: a 512-char password is
  redacted whole, a 513+ one is declined as a UNIT, and a 6,000-case in-process
  fuzz asserts that a decline is always explained structurally.

### Wiring Check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `redact_secrets` (the write-chokepoint scrubber) | Python API | this change — a new kind in `_SECRET_SHAPES`; signature and contract unchanged | ✅ |
| Session `:Source` (summary/topics), turn `:Point`s, both extractor lanes, the v1/v2 `commit_session` siblings | persistence sinks | already routed through the SAME function by #4911 — no new call site | ✅ (no change needed) |
| `capture_redactions` receipt | metric | the count flows through the existing path; one count per URL | ✅ |
| `tests/test_capture_secret_redaction_4911.py` | the acceptance surface | `CASES` gains a `connection_url` row (its exhaustiveness assertion requires it) and the pinned-decision prose test is updated | ✅ |
| `config/ci-surfaces.yml` | CI selection | the new test file registered in `core` + a `durations` entry; `tools/ci_selection.py --integrity` green | ✅ |
| MCP tool registry / `TortoiseSDK` public surface | contract | untouched | ✅ |

## Residuals (recorded, not silent)

1. A password **longer than 512 characters** is not matched at all — no real
   credential body reaches 512 (bcrypt 60, base64url 43, a 256-bit hex key 64, a
   512-bit one 128) and a JWT used as a password is claimed by `jwt` first. It is
   a whole-candidate miss, never a truncated prefix.
2. A **custom scheme** is not covered — the closed allow-list's inherent property.
   One line to add one.
3. A raw **`?`/`#` inside a password** is read as the query/fragment start it is
   in URL grammar, so `docker://:P@ssw0rd?x@host` is not matched (the
   percent-encoded `%3F` form is). `tools/ask_shape_rate.py` records the same
   reading against the graph client.
4. A URL whose credential is a **bare userinfo token with no `:`**
   (`https://<token>@host`) is not matched here; the token is claimed by its own
   vendor rule where one exists (`ghp_`).
5. **The delimiter trade (#5326 review).** A raw `'`/`,`/`;` (and
   `"`/`<`/`>`/`{`/`}`) is treated as ending the rule's URL. Inside a password it
   declines the candidate whole (never a prefix, the #5470 direction); AFTER the
   authority's `@` it bounds the URL, so `postgres://user:abc@def,ghi@host`
   matches `postgres://user:abc@def` (password `abc`, host `def`) and the
   trailing `,ghi@host` is a separate token. The stricter RFC 3986 reading (`,`
   is a legal reg-name character, so `abc@def,ghi` is one userinfo) is the recall
   gap accepted here.
6. **The count on this corpus moves.** ~167 production turn nodes quote this
   repo's own documented dev URI, and it is now matched. That is the intended
   consequence of the decision below, not an accident.

## `OVERRIDES:` — the one departure from the grain

> **OVERRIDES:** the common scanner default of *sparing known test/dev values and
> loopback hosts* (trufflehog's `detectLoopback = false`, `allowKnownTestSites`) —
> `docker://:falkordb@localhost:6379/<graph>` IS redacted here, because `docker://`
> is Tortoise's own `TORTOISE_DB_URI` scheme and its production form is
> `docker://:password@host`; sparing the dev spelling would leave the product's
> own primary credential form in cleartext (`capture_redactions: 0`), and a false
> negative leaks while a false positive costs one span and one count.

The cost is bounded and visible: only the password span is replaced, so 167 nodes
lose one field and keep their engine, host, port and graph name.
