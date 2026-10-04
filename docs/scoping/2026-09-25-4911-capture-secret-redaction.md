---
title: "Scoping #4911 — capture-path secret redaction (double diamond + the local-spool ruling)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted
aboutObjects: capture, redaction, secret-hygiene
created: 2026-09-25
---

# Scoping — #4911: the capture path has no secret redaction

**Lane:** Lane 2 Extractor pipeline · **Branch:** `fix/4911-capture-secret-redaction`
**Tier:** standard (`complexity:standard`) · **Domain:** Complicated (established patterns: a
regex scrubber at one write chokepoint, not a new mechanism).

## Problem diamond

### Divergence — three framings, and which one the measurement supports

| Framing | What it would have us build | Verdict |
|---|---|---|
| **A. "Scan the graph and purge what is found"** | A periodic scanner + deletion job | **Rejected.** The measured graph has zero anchored matches (see below), so a purge has nothing to do — and a scanner cannot protect a write that happens between scans. It also cannot be a control for the *next* paste. |
| **B. "Refuse the capture"** (fail closed on a suspected secret) | Reject/`4xx` a session containing a credential | **Rejected.** It destroys the session's memory to protect one span, contradicts the capture-consent boundary (#3615: consent decides *whether* a transcript is uploaded), and invents a new failure mode for every session that quotes documentation containing a key-shaped example. |
| **C. "Redact before persistence, at the write path"** | Anchored match → visible marker, count recorded | **Confirmed.** Keeps the session, removes the fact, and sits where the trust domain changes (local → hosted multi-tenant). |

### Root cause (converged)

The capture contract has no *what survives* filter. `_write_capture_turns` is the single batched
`UNWIND`/`MERGE` every turn of every lane passes through (it replaced the two per-lane loops in
#3086), and it stored the text it was handed, verbatim. The absence of a leak is timing — capture is
session-end, and a live Jev key was pasted on 2026-09-23 — not protection.

**Measured (production, `org_3326a01ea34ae595d84de5d8f9`, 2026-09-25, this lane):**
**21,281 of 21,281** turn nodes read — every page read to the host's `hasMore=false`
(the criterion's "unpaginated" means *not a truncated page*; the transport is paged and was read to
the end), tombstone-inclusive (`include_retracted=True`), the anchored table run over each node's
content → **0** anchored credential matches, while **170** nodes contain the literal `sk-`
substring, every one prose (`risk-`/`disk-`/`task-`). The prior scan recorded on the issue covered
10,000 of 19,960; the posted re-scan comment is the complete one. Two of the three numbers moved
between scans only because the corpus grew (21,265 → 21,277 → 21,281 nodes) — the zero did not.

One measurement changed the design twice, and the second pass removed a rule: the first run of this
scan matched **176** nodes, every one a non-credential (167 of them this repo's own documented dev URI,
9 a URL-shape fixture table), because the new `connection_url` rule was anchored on a scheme
(`\w+://`) rather than on a host. Requiring a *dotted* hostname after the `@` dropped all 176 — and
review then showed that was the *wrong* trade: the dropped class contains real credential shapes
(single-label hosts: `@dbserver`, `@cache`, the docker-compose form; plus IP literals). Restoring a
password requirement with bounded user/password classes failed the other way (a password containing
`/` or exceeding the bound is missed; a greedy class swallows surrounding text and destroyed JSON keys
and email values around a URL). Three revisions, three different failures, and on the real corpus the
rule's matches were never once a credential other than our own dev URI ⇒ **the rule was removed**
rather than shipped noisy-or-incomplete. Its scheme is arbitrary, so there is no vendor prefix to
anchor on, and this module's rule is anchored shapes only. The gap is filed as its own issue; the
final scan above is the table as shipped. This is the same evidence discipline that made `sk-`
anchored in the first place.

### Contradiction test (run FIRST, per `AGENTS.md`)

| Candidate recorded decision | Does it govern this? |
|---|---|
| **#3870 / #3853 / #3872 (owner, 2026-09-17)** — the local spool is type-2 user data, kept until the user deletes it; no TTL | Governs **retention**, not redaction. Redacting is not deleting and not a timer ⇒ **no contradiction**, no reopen needed. |
| **Owner, 2026-09-23** (STORAGE-ARCHITECTURE §13): *"the narrative is not something we're suggesting to store in the graph (same as raw) but store in supabase"* | Governs **where raw turns live**. The code today writes turn nodes to the graph, so **code and decision disagree** — filed as a finding on #4911 (it is the reason this issue's exposure exists at all). It does not forbid redacting on the write path. |
| **#3875 (SDK/MCP surface approval)** | No surface changes here: no tool, no public SDK method added or removed. |
| **#4897** (the 5,000-char silent truncation on this same path) | Adjacent, not governing — but it sets the *shape*: a loss on this path must be visible. |

No decision forbids the fix, and none requires it ⇒ this is a **change, not a reopen**.

## Solution diamond

### Divergence — where the control sits

| Option | Trade-off | Verdict |
|---|---|---|
| **1. Inside `_write_capture_turns`** (the issue's own proposal) | The one place a row is written — but both lanes compute `turn_embs` from `_capture_turn_texts(windowed)` **before** calling it. Redacting there stores `[REDACTED:…]` while the vector describes the raw secret, violating the #4194 invariant ("the vector can never describe different text than the node holds"), and it makes every session containing a secret permanently UNCONFIRMED for the #4675 spool confirmation (client expects raw, server holds marker → mismatch → never files) | **Rejected — it breaks two live invariants.** |
| **2. Inside `_capture_turn_texts`**, the ONE definition of the stored turn text that the writer itself calls | Same single chokepoint (every lane, every turn), and the encoded text, stored text and `content_hash` are the same string by construction; the #4675 client computes its expectation with the same function, so the two cannot drift | **Chosen.** |
| **3. In each caller (SDK + hosted)** | Two sites, the #1532/#2813 drift class the codebase has spent issues deleting | **Rejected.** |

### Convergence

Option 2. The redaction table lives in `tortoise/security.py` (stdlib-only, no cycles, already the
home of the other shared security semantics) so #5002 can adopt the same scrubber for its own
surface; the pattern set is anchored-shape-only, ordered (`sk-ant-…` before `sk-…`), and emits a
visible `[REDACTED:<kind>]` — never a `***` and never a truncation. The count is written to the
`Session` as `capture_redactions` inside the same batched statement (no extra round trip) with a
trailing `SessionRecorded` so a rebuild restores it, and surfaced on BOTH lanes' receipts.

**Residual — CLOSED in this change: the LLM extraction leg.** The first scope left it out: the raw
conversation was handed to the session LLM, and a claim that *echoes* a pasted credential is written
by `create_point` — a different write path with no scrubber. Review reproduced it end to end (a
non-episodic Point holding the raw key, on a capture that reported a redaction), so it is not a
residual: it is the same defect one write path over. Both extractor lanes now take the scrubbed
conversation, so the model never receives the value and an echoed claim is marker-only (#5294,
filed and fixed here). This also closes the hole the local-spool decision would otherwise rest on:
see below.

**Residual — CLOSED in this change: the PEM scan cost.** The two PEM rules were replaced by ONE
lazy body with an END-or-end-of-text alternation, which makes every match consume to its own END and
the scan linear in the text (measured: 40–96 ms before on a 5,000-char adversarial turn, 0.3 ms
after; #5296, filed and fixed here). The fail-closed behaviour — a header with no END is still
redacted — is preserved by the alternation, and a test asserts the dangling-header case.

**Residual, deliberately kept:** a credential split by a literal newline *inside* one turn
(`sk-proj-<half>\n<half>`) is not matched, because every body class excludes whitespace and allowing
whitespace inside a body turns any token-shaped prefix followed by prose into one giant match. The
sentence-segmenter split (the case that mattered — it silently broke every JWT) is fixed by scrubbing
per turn *before* assembly; the literal-newline case is recorded here rather than papered over.

**Residual, deliberately kept (2):** a credential the 5,000-char window cuts *mid-body* can leave a
PREFIX in the stored turn, uncounted and unmarked. The cut happens in `_capture_turn_window` on both
write lanes before the writer sees the text, so the scrubber cannot reach the removed half. What
survives is a prefix of one shape — never the whole value (a shape still meeting its body floor is
redacted; the AWS secret half is a 40-char value whose 40-char prefix is the value, so the boundary
is the value's own length). Pinned by
`test_an_over_long_turn_matches_between_client_and_server`, which asserts the client and the server
produce the SAME stored text for a cut turn — the property that matters, because a divergence there
would leave the #4675 spool confirmation comparing unequal forever and deferring the entry for good.

**Residual, deliberately kept (3):** the JWT rule allows `\s*` around its dots (a wrapped JWT must
still be caught, and the segmenter's space-rejoin makes that the common case), so a `eyJ…` blob
followed by two long dot-separated words can absorb that prose. Visible in the marker, not a leak;
narrowing it would cost the wrapped-JWT recall the rule exists for.

**Residual — CORRECTED by review (cycle 1):** this paragraph used to say `_materialize_session_source`
"still runs on the event loop". It does not — this change moved it onto `_CAPTURE_EXECUTOR`, with the
scrub cost as the stated reason. What review DID find is that two other consumers of the new scrub had
been left on the loop: `_capture_turn_texts(windowed)` for the embedding batch and again for the
entity-linking pass — the scrub has a measured, documented cost of ~3 s/MB, and a legal-maximum
500x5,000 capture is 2.5 MB of client-controlled text, so that was seconds of CPU on the loop per
capture, the #3060/#3086 freeze class. Both now run off the loop, once, and the writer and the linker
reuse the result. **No capture-path scrub runs on the event loop.** The scrub itself is linear
(measured 0.97 s @220k, 1.51 s @440k); its 2x-input cost ratio is ~2, and the binding test asserts
that scaling rather than a wall-clock threshold.

**Residual — cycle 2, MEASURED and PINNED: the `jwt` rule will not match a token whose HEADER
segment exceeds 512 characters.** Cycle 1 made the rule linear by excluding `_`/`-` from its
lookbehind, which also stopped matching a JWT glued after one of those characters — a LEAK against a
shape that was covered before the change, caught by cycle 2 (`pre='_' -> {}` where the pre-change
rule returned `{'jwt': 1}`). The fix bounds the FIRST segment at 512 characters instead: that
restores the recall and keeps the scan linear (the per-candidate work is capped, so the total is
O(text) however many candidates occur). The price is this bound — a JWT whose base64url header
exceeds 512 chars (a header carrying an embedded `jwk`/`x5c`) is not matched. Real headers are 36
chars (`{"alg","typ"}`) to ~60 with `kid`.

**Residual — cycle 2, MEASURED and PINNED: the `private_key` label class is bounded at 40
characters.** An unbounded label class in front of a REQUIRED `PRIVATE KEY-----` suffix is quadratic
whenever the suffix is absent — at every `-----BEGIN ` the class consumed the tail and backtracked
for the suffix (0.018 s @11k → 0.242 s @22k → 1.276 s @44k → 5.752 s @88k). Bounded at 40 it is
linear (0.0024 s for the same 88 k input). Real PEM labels are `RSA`/`EC`/`OPENSSH`/`DSA`/`ENCRYPTED`,
so 40 is generous.

Both are bound by `test_every_rule_scans_linearly_on_adversarial_input`, which asserts the SCALING of
the three adversarial families (2x input < 3x time) *and* the leading `_`/`-` recall — so neither
half of the linearity-vs-recall trade can be undone silently. That test replaces
`test_the_jwt_rule_scans_linearly_on_adversarial_input`, which cycle 1 shipped and which could not
discriminate: its input's every `eyJ` after the first is preceded by `A`, so the lookbehind rejected
it before any body work ran, and it passed with the guard reverted.

**Residual, FILED not fixed (outside the declared T1–T7 surface): the confirmation comparison is
version-sensitive.** `session_confirm.expected_turns` builds the expected stored text with the
CLIENT's redaction table and `confirm_capture` requires an exact match. Before this change the stored
text was a version-independent function of the posted turns; now it depends on `_SECRET_SHAPES`, which
lives independently on an installed client and a deployed server. A skew — an older client against an
upgraded server, which is the direction a table extension moves — makes a turn with a shape the two
disagree on compare unequal, so its `capture_spool` entry returns the retryable verdict, defers with
backoff, and never terminalises (the #4675 symptom, re-entered through version skew rather than through
the writer/reader mismatch #4923 fixed). Filed as #5394 with a verified reproduction rather than
fixed here: the fix (accept a served body that differs from the expectation only where a
`[REDACTED:<kind>]` span stands in for text the other side holds) loosens the exact-match guard, so it
needs its own review rather than riding a bounded security cycle. Also recorded in #5394: the #3086
guard (`tests/test_capture_loop_responsiveness.py`) counts on-loop QUERIES and is structurally blind
to a query-free CPU regression, which is exactly why the on-loop scrub above shipped green.

### Three persistence consumers, one control

Review reproduced a P0 the original scope missed: `_capture_turn_texts` is where the stored **turn**
text is defined, but the same capture also materializes the session `:Source`, whose `summary` is the
first substantive utterance and whose `topics` are the six most frequent content words — i.e. turn
text, one property over. Redacting only the turn Points left the credential in the graph on the SAME
write. A second P0 landed on the Source itself: scrubbing the *assembled* transcript is too late,
because it is sentence-split and newline-flattened first, so a JWT arrived split into three
space-separated fragments that no pattern matches. Both are closed by scrubbing per turn at ONE
function (`sdk._redact_turn_contents`), which the turn store, the Source and both extractor lanes
route through — and the pinning test enumerates those six consumers by name (the turn store, the
Source, both extractor lanes, and the public `commit_session`'s v1 **and** v2 siblings), so a new
persisting sink cannot appear unnoticed. The v1 and v2 commit siblings are in the set because review
found `_commit_session_v2` uncovered and `_commit_session_v1` — the documented reversibility seam —
still reachable; both drive the same extractor and POST derived payloads to `/v1/sessions/commit`,
whose writes carry no scrubber of their own. Neither uses the `cap` (the v1 path has no window at
all, matching the extractor's own per-turn bound), so the extractor remains the only place a turn is
bounded on those paths.

### The decision the issue handed this lane: is the LOCAL raw store in scope?

**Decision: NO — `tortoise/capture_spool.py` is out of scope, and the exclusion is pinned by a
test so it cannot change silently.** Reason, in order:

1. **It cannot work.** The spool's input is the harness's own transcript
   (`~/.pi/agent/sessions/…/*.jsonl`, `~/.claude/projects/…/*.jsonl`), which retains the bytes
   verbatim and is not ours to rewrite. Redacting only our copy removes nothing from the machine —
   it is fidelity loss with no security gain.
2. **The single-chokepoint requirement forbids it.** Acceptance criterion 4 asks for the control at
   the ONE turn-write chokepoint, not duplicated per call site. A second control site in the spool is
   that duplication.
3. **It is the wrong trust domain.** Redaction is a boundary control (local → hosted multi-tenant).
   The spool lives entirely inside the local domain, and the spool is the replay payload of record
   for the deferred POST — making it lossy changes what is filed.
4. **The replay path still passes the chokepoint, and that was verified rather than assumed.** The
   deferred POST replays the spooled turns to the hosted `/v1/sessions`, which scrubs server-side
   (`_capture_turn_texts` and `_materialize_session_source`) before writing. Review found the one way
   this reasoning could have been false — the same request also fans the raw bytes into the
   extractor — and that hole is now closed (see the extraction residual above), so the local raw
   store no longer reaches the graph unredacted by ANY path.

**OVERRIDES: not applicable** — keeping a local raw transcript unredacted is the common/industry
default (Aider, Codex, Claude Code, Pi all keep raw local transcripts), so this ruling runs *with*
the grain. Nothing is being overridden, and no reopen is required.

### Adversarial Threat Surface (declared — binds this review to the 2-cycle adversarial bound)

**Control under test:** a credential pasted into a captured turn must not survive into the hosted
graph. **Attacker model:** the *content of a turn* is attacker-controlled — the control is the only
thing between that content and persistence, so "can the pasted text get itself stored anyway?" is the
whole question.

**In-scope bypass classes** — each must have a test that FAILS without the fix (verified by
neutralizing the scrubber and watching the class red), plus green CI:

| # | class | tests that bind it |
|---|---|---|
| **T1** | **Shape evasion** — a real token the anchored rule misses on a boundary (`\b` vs lookahead), an internal `-`, or a body floor | `test_every_credential_shape_is_redacted_end_to_end`, `test_a_credential_touching_a_word_character_is_still_redacted`, `test_anchored_shapes_do_not_redact_prose` |
| **T2** | **Fail-open on malformed/partial shapes** — END-less PEM, cap-truncated PEM, JWT split by the sentence segmenter, app-level Slack tokens | `test_unterminated_and_partial_shapes_do_not_fail_open`, `test_a_multipart_credential_is_not_left_in_the_source_as_fragments` |
| **T3** | **Second persistence sink** — the same credential reaching the graph by a path other than the turn store: session `:Source` (summary/topics), the extractor→`create_point` leg, the v1/v2 `commit_session` siblings, a caller-supplied `summary=` | `test_the_extraction_leg_writes_the_marker_not_the_credential`, `test_control_lives_at_the_single_stored_text_chokepoint`, `test_a_caller_supplied_summary_is_scrubbed`, `test_the_source_sink_scans_a_bounded_window` |
| **T4** | **Cap / ordering bypass** — `cap` must bound the RESULT (not only the scanned text), and client and server must cut-then-scrub identically or the #4675 confirmation never files | `test_a_capped_scan_truncates_the_text_it_returns`, `test_an_over_long_turn_matches_between_client_and_server` |
| **T5** | **Non-string content coerced past the scrubber** and later `str()`-ed by a downstream consumer | `test_the_source_sink_scans_a_bounded_window` |
| **T6** | **Re-match / double-count on the second pass** — the visible marker re-matched, so wording or the recorded count depends on how many times the helper ran | `test_redaction_is_idempotent_under_the_capture_double_pass` |
| **T7** | **Scanner self-DoS** — a superlinear rule makes the control too costly to run on client-controlled text | `test_every_rule_scans_linearly_on_adversarial_input` (all three adversarial families, with a 2x-input scaling assertion and a recall check) |

**Explicitly OUT of scope** (declared and named, so they are not chased inside the bound): rotating
the already-pasted key (owner); the capture **consent** mechanism (#3615); a password-bearing
connection URL, which has no anchor both precise and complete (**#5326**); an AWS key id with a
character appended and no separator, and two credentials with no separator at all (documented
residuals below); secrets in the harness's own local transcript and in the local spool (the decision
above); and the LLM provider's own server-side logs for pre-fix transmissions, which cannot be
recalled.

**Bound: 2 cycles** (the `code-review` skill's adversarial bound). In-scope findings that survive
cycle 2 are filed and recorded, never iterated further; findings outside the declared surface are
filed and not chased.

## Known residuals (recorded, not silent)

1. **A password-bearing connection URL is not covered** (`postgres://user:pw@host`) — the rule was
   built, measured three ways and **removed**, because no anchor made it both precise and complete
   (see the problem section). Filed as its own issue with the measurements; the alternative was a
   rule that is either noisy (and so makes the redaction count meaningless) or incomplete (and so
   leaks while looking like coverage).
2. **An AWS access-key ID with a character appended and no separator** (`AKIA<16>X`) is not matched:
   the body is an exact 16 with an alnum terminator. The `{16,}` alternative over-matched ordinary
   all-caps prose (`ASIA`/`ABIA`/`ACCA` are real word prefixes: `ASIAPACIFICREGION…`), and a rule
   that fires on prose is worse than a documented miss on a shape that requires a key and another
   alnum run to be *concatenated*. The same shape behind an `_` **is** matched.
3. **Two credentials with no separator at all** (`ghp_<36>XYZghp_<36>`) — the first is matched and
   consumed; the second's body survives. Contrived (no real transcript form), and fixing it reopens
   the word-boundary class that the `_suffix` false-negative came from.
