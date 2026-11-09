"""E2E-1 source-level guard (#770 — plan Task 2): the tenant-provision Edge
Function must write the master list into Supabase ONLY.

The live E2E-1 registry assertion (registry_control_plane node count == 0
after signup) runs in the pre-deploy gate (plan Task 10). This test guards
the same contract at the source level so a regression can never ship: the
Edge Function must call provision_team (the atomic Supabase RPC), must NOT
call /internal/provision (the old registry writer) or update_user_team (the
old membership writer), must keep the data-plane REAL starter seed
(#2360: /internal/starter-seed — never the demo sample auto-seed), and must
compute lookup_hash via the shared TS mirror (parity with tortoise/auth.py).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from tests._verdict import (
    NODE_FLOOR_STRIP_TYPES,
    host_capability,
    require_node_floor,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
EDGE_FN = _REPO_ROOT / "supabase" / "functions" / "tenant-provision" / "index.ts"
SHARED_LOOKUP = _REPO_ROOT / "supabase" / "functions" / "_shared" / "lookup.ts"

# Functions with NO browser caller, and therefore NO origin allowlist. Module-level
# because TWO guards read it and they must not disagree: the parity sweep compares
# the discovered non-declaring set against it EXACTLY (so a NEW function with no
# allowlist, and one of these re-growing a CORS surface, both red), and
# test_edge_function_declares_no_origin_allowlist pins this function's membership
# (so deleting a name from the set, the way a future author would "fix" the sweep,
# reds too). `_shared` is imported, never served.
SERVER_ONLY_FUNCTIONS = frozenset({"_shared", "tenant-provision"})

# The version `module.stripTypeScriptTypes` was introduced in — a HIGHER band
# than `NODE_FLOOR_STRIP_TYPES` (22.7, the `--experimental-strip-types` FLAG),
# which this file no longer passes. Not used for a numeric compare (the API is
# PROBED, and a numeric floor would wrongly admit 23.0/23.1): it is here because
# the probe's failure message and the docstring must name a version a reader can
# act on.
NODE_FLOOR_STRIP_TYPES_API: tuple[int, int] = (22, 13)

_STRIP_SCRIPT = (
    "const m=require('node:module'),fs=require('node:fs');"
    "fs.writeFileSync(process.argv[2], "
    "m.stripTypeScriptTypes(fs.readFileSync(process.argv[1],'utf8')));"
)


def _node_has_strip_api(node: str) -> bool:
    """PROBED, never assumed — the two APIs do not share a version floor.

    Measured via `npx node@<v>`: absent on 22.12.0 and 23.1.x, present on
    22.13.0. A numeric compare against (22, 13) would wrongly admit 23.0/23.1,
    so the capability itself decides.
    """
    probe = subprocess.run(
        [
            node,
            "-e",
            "const m=require('node:module');"
            "process.stdout.write(typeof m.stripTypeScriptTypes)",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return probe.stdout.strip() == "function"


def _parse_verdict(node: str, source: str) -> str | None:
    """THE guard mechanism: strip types with the API, `--check` the JavaScript.

    Returns None when `source` parses, else the failing step's output. Split out
    so `test_the_parse_guard_rejects_broken_typescript` can prove the mechanism
    is able to FAIL at all — a parse guard that cannot fail is a no-op.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / "subject.ts"
        src_path.write_text(source)
        stripped = Path(tmp) / "subject.mjs"
        proc = subprocess.run(
            [node, "-e", _STRIP_SCRIPT, str(src_path), str(stripped)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return f"type-strip step (exit {proc.returncode}):\n{proc.stderr}"
        result = subprocess.run(
            [node, "--check", str(stripped)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return f"--check step (exit {result.returncode}):\n{result.stderr}"
    return None


def _require_parse_mechanism(what: str) -> str:
    """The node binary this guard can use, or a SKIP naming what is missing.

    Two distinct floors, deliberately: `require_node_floor` keeps #4916's
    absent/unprobeable contract at the shared 22.7 band, and the API probe is
    what actually decides (a host-capability gap SKIPs, never REDs — #5049
    rule 5).
    """
    require_node_floor(NODE_FLOOR_STRIP_TYPES, what=what)
    node = shutil.which("node")
    host_capability(
        lambda: _node_has_strip_api(node),
        what=f"module.stripTypeScriptTypes for {what}",
        remedy=(
            f"use Node >= {NODE_FLOOR_STRIP_TYPES_API[0]}."
            f"{NODE_FLOOR_STRIP_TYPES_API[1]} (the API was added in v22.13.0 / "
            "v23.2.0) — the guard cannot run on this host"
        ),
    )
    return node


def test_edge_function_parses():
    """The Edge Function must at least PARSE as TypeScript — a syntax error
    would make every signup 500 at deploy time. Regression guard for the
    duplicate-`const pepper` P0 caught in review (PR #847): string-assertion
    tests above cannot see redeclarations, and CI has no deno/tsc step, so
    node's type-stripping is the cheapest gate. A present-but-incapable host
    SKIPs (#4916/#5049 rule 5), it does not RED. Skips when node is absent.

    WHY THE TWO STEPS, AND WHY THERE IS NO ONE-STEP FALLBACK: `node
    --experimental-strip-types --check <file>.ts` was meant to strip types
    before parsing, so it was the whole check in one call. Measured over real
    runtimes (`npx node@<v>`, this exact file): `--check` NEVER type-strips, so
    it fails in BOTH directions and was never a valid guard on any node —

      * **false RED on 24.21.0** — a CLEAN file exits 1 with "Unexpected strict
        mode reserved word" at the first `interface`, because `--check` parsed
        the .ts bytes as JavaScript. Reproduced in this repo's tree (exit 1),
        which made the guard red on a healthy tree and therefore
        indistinguishable from version noise.
      * **it never DISCRIMINATES — it answers on 24.21.0 with a constant per
        FILE SCOPE, not per input.** Measured, both directions, clean file vs
        the same file with `const oops: number = ;` appended:
        - under a `package.json` whose `"type"` is `"commonjs"` → **exit 0 for
          both**, i.e. it does not parse at all (a silent no-op that would pass
          a syntax error);
        - with no `package.json` in scope (this repo's tree, `/var/tmp`, a fresh
          `$HOME` dir) → **exit 1 for both**, the false RED above.
        Reproduce both, self-contained — NOTE the scope-sensitive `mktemp`:
        `d=$(mktemp -d "$HOME/.tmpcheck.XXXXXX")` (a bare `mktemp -d` lands in
        `/tmp`, which may itself be a commonjs scope — see below — and the
        repro then silently shows only the second mode); `cp
        supabase/functions/tenant-provision/index.ts "$d/index.ts"; node
        --experimental-strip-types --check "$d/index.ts"` → exit **1**, then
        `printf '{"type":"commonjs"}' > "$d/package.json"` and re-run → exit
        **0**, broken input included.
        Either constant is useless to a guard, so the one-step form cannot be
        trusted on ANY host; that is the conclusion this bullet exists for.
        (Two earlier drafts of this note got the CAUSE wrong in opposite
        directions — one called it a location rule, one denied the location
        difference — and both were reading the same real effect: `/tmp` on a
        shared box carried `agent-infra`'s `package.json` (`"type":
        "commonjs"`), so a `/tmp` repro landed in the silent-no-op scope while
        the checkout landed in the false-RED one. Pinning the SCOPE, not the
        location, is what makes the claim reproducible.)
      * **false GREEN on 22.10.0, 22.13.0 and 23.1.x** — the same clean file
        exits 0, and still exits 0 after appending `const oops: number = ;`. A
        broken function would have merged green.

    So the types are stripped explicitly with `module.stripTypeScriptTypes` and
    the resulting JavaScript is what `--check` sees: measured, clean input
    exits 0 and broken input exits 1 at the strip step on 22.13.0 and 24.21.0.
    The one-step form is only reachable on hosts WITHOUT that API (22.7–22.12,
    23.0–23.1) — exactly the band where it is a false GREEN — so those hosts
    SKIP with a named remedy instead of running a guard that cannot fail.
    """
    node = _require_parse_mechanism("the tenant-provision edge-function parse check")
    failure = _parse_verdict(node, EDGE_FN.read_text())
    assert failure is None, f"tenant-provision/index.ts does not parse — {failure}"


def test_the_parse_guard_rejects_broken_typescript():
    """NON-VACUITY for `test_edge_function_parses`: the mechanism must REJECT
    broken TypeScript, or the guard above passes everything.

    This is not hypothetical. Measured on 22.10.0 / 22.13.0 / 23.1.x, the
    one-shot `--check --experimental-strip-types <file>.ts` exits **0** for a
    file ending in `const oops: number = ;` — it accepts broken code, so a
    guard built on it prints green forever (it was the previous fallback for
    exactly the band that lacks the strip API). The strip-then-check mechanism
    exits non-zero at the strip step, which is what this test pins.
    """
    node = _require_parse_mechanism("the parse guard's non-vacuity check")
    broken = EDGE_FN.read_text() + "\nconst oops: number = ;\n"
    failure = _parse_verdict(node, broken)
    assert failure is not None, (
        "the parse mechanism ACCEPTED a deliberately broken copy of "
        "tenant-provision/index.ts — it guards nothing; check that the guard is "
        "not invoking a form that skips type stripping"
    )



def test_edge_function_writes_supabase_only():
    """E2E-1: zero registry writes from the signup path (source contract)."""
    src = EDGE_FN.read_text()
    assert 'rpc("provision_team"' in src, (
        "Edge Function must write Supabase via the provision_team RPC"
    )
    # The string may appear in explanatory comments; the CALL must not.
    assert "fetch(`${fastApiUrl}/internal/provision`" not in src, (
        "Edge Function must not call the registry-writing /internal/provision"
    )
    assert 'rpc("update_user_team"' not in src, (
        "Edge Function must not call the removed update_user_team RPC"
    )
    # Data plane stays: the real starter seed creates the team's knowledge-graph
    # namespace (FalkorDB) — that is NOT a registry write.
    assert "/internal/starter-seed" in src, "Edge Function must keep the starter seed"
    # #2360 re-spec: the fake demo auto-seed is GONE — a fresh org must never
    # receive sample Points counted as its own data. The fetch URL is the
    # contract (a stray call must never regress).
    assert "`${fastApiUrl}/internal/demo`" not in src, (
        "Edge Function must not call the demo sample auto-seed (#2360)"
    )
    # lookup_hash must be computed via the shared TS mirror (P1-1 parity).
    assert "lookupHash" in src, "Edge Function must compute lookup_hash"


def test_starter_seed_logs_non_ok_responses():
    """#1860 (P3-1) carried to the #2360 starter seed: a non-2xx
    /internal/starter-seed response must be LOGGED, not silently swallowed.
    The old fire-and-forget `.catch()` covered only transport/abort errors —
    an HTTP 500 body resolved 'successfully', so a failed seed left the
    first-timer's graph silently missing starter data. The response must be
    bound and checked with !ok (source contract)."""
    src = EDGE_FN.read_text()
    # the starter-seed fetch binds its response (not fire-and-forget)
    assert "const starterRes = await fetch(`${fastApiUrl}/internal/starter-seed`" in src, (
        "starter-seed fetch must bind its response (starterRes) so !ok is checkable"
    )
    # transport/abort failures still log
    assert 'console.error("Starter seed failed:", e)' in src
    # HTTP errors (4xx/5xx) log the status — never silently pass
    assert "!starterRes.ok" in src, (
        "starter-seed must check !starterRes.ok — a 500 body resolving 'successfully' "
        "is the #1860 P3-1 bug"
    )
    assert "starterRes.status" in src, "non-ok starter-seed log must include the status"
    # the provision RPC has already committed — the failure must NOT fail the
    # whole provisioning (the user can still be onboarded)
    assert "return json(response, 201" in src


def test_starter_seed_carries_real_identity_data():
    """#2360: the starter seed is REAL data — the edge fn must pass the
    org name (user-confirmed), the person's own auth identity (user_id /
    email), and the user's display name when present (never a silently
    email-derived name, never a placeholder)."""
    src = EDGE_FN.read_text()
    # org_name = the wizard-typed org name (safeName) — real, confirmed
    assert "org_name: safeName" in src, "starter seed must pass the org name"
    # the person identity refs ride the VERIFIED caller (never client JSON)
    assert "person_user_id: user_id" in src
    assert "person_email: email" in src
    # display name is passed only when the user actually has one
    assert 'starterBody.person_name = display_name.trim()' in src


def test_edge_function_uses_shared_lookup_mirror():
    """The TS mirror import must exist and stay pure (runs in Deno AND node —
    the parity test imports it; Deno-only APIs would break the parity run)."""
    assert SHARED_LOOKUP.exists(), "supabase/functions/_shared/lookup.ts missing"
    assert EDGE_FN.read_text().count("_shared/lookup.ts") == 1
    src = SHARED_LOOKUP.read_text()
    assert "Deno.env" not in src and "Deno.serve" not in src, (
        "lookup.ts must be pure (no Deno API calls) — the node parity test imports it"
    )
    assert "crypto.subtle.digest" in src


def test_shared_lookup_implements_plan_construction():
    """The construction must be the plan's exact scheme: SHA-256(pepper + key)
    — pepper FIRST (plan P1-1). The Python side is locked to the same order
    by tests/test_lookup_hash.py + the parity test."""
    src = SHARED_LOOKUP.read_text()
    assert "pepper + key" in src
    # the digest input must be pepper concatenated BEFORE the key
    assert "pepper + key" in src.replace(" ", "") or "encode(pepper + key)" in src


# ── server-only guards (#1120 — supersedes the #1111 CORS guards) ─────────────
# HISTORY: the welcome page used to call tenant-provision DIRECTLY from the
# browser (#527/#802), so a `Content-Type: application/json` +
# `Authorization: Bearer` POST forced a CORS preflight and the 2026-08-13
# incident blocked every signup until #1111 added an origin allowlist,
# an OPTIONS answer and ACAO on every response path.
#
# That caller is GONE. welcome.html's provisioning pipeline is dead (#1566) and
# the dashboard reaches this function through its same-origin BFF route
# (`website/apps/dashboard/functions/api/provision.ts`, #3501/#4054), which
# mints the access token server-side from the `__Host-session` cookie and
# STRIPS `Origin` before forwarding. #1120 therefore deletes the whole CORS
# surface — no allowlist to maintain, no preflight to answer, no
# `Access-Control-*` header on any path — and makes the absence ENFORCED: an
# origin-bearing request is refused 403. These guards pin that shape; the
# runtime behavior is executed by tests/test_tenant_provision_server_only.mjs.

# ── #2406: onboarding-call offer email (post-provision trigger) ────────────

def test_edge_function_fires_onboarding_email_after_provision():
    """The provisioning door must fire POST /internal/onboarding-email (the
    #2406 one-time onboarding-call offer) for every NEW hosted signup."""
    src = EDGE_FN.read_text()
    assert "/internal/onboarding-email" in src, (
        "Edge Function must fire the onboarding-email internal endpoint"
    )
    # Fires after the starter-seed block, still inside the handler (provision
    # RPC already committed).
    assert "await fireOnboardingEmail(orgId, display_name);" in src, (
        "onboarding email must be fired after provisioning, passing the "
        "PERSON display_name"
    )
    assert src.index("fireOnboardingEmail(orgId, display_name)") > src.index(
        "Starter seed failed"), (
        "onboarding email must fire after the starter seed (independent of it)"
    )


def test_edge_function_onboarding_email_passes_person_name_not_safe_name():
    """The greeting must derive from the PERSON display name (caller/body) —
    NEVER the org slug (safeName): the org name is wizard-typed and
    whitespace-free; it is not a person's name (scope-doc §Personalization)."""
    src = EDGE_FN.read_text()
    # Scope the check to the onboarding POST body itself (safeName legitimately
    # appears elsewhere — e.g. the provision_team RPC body p_org_name).
    body_start = src.index("const body = JSON.stringify(")
    body_chunk = src[body_start:body_start + 400]
    assert "display_name: personDisplayName ?? undefined" in body_chunk, (
        "onboarding body must carry the person display_name key"
    )
    assert "safeName" not in body_chunk, (
        "the org slug must never be passed as the email greeting source"
    )
    assert "org_name" not in body_chunk, (
        "the onboarding body must carry org_id + display_name only"
    )
    # The call site passes the PERSON display_name — not safeName.
    assert "await fireOnboardingEmail(orgId, display_name);" in src
    assert "fireOnboardingEmail(orgId, safeName)" not in src


def test_edge_function_onboarding_email_retries_and_never_fails_provisioning():
    """The #2406 email POST is 2 attempts (0s/+2s), bounded, logs every
    non-definitive outcome, and NEVER throws — provisioning has already
    committed, so a failed offer email must never fail the signup."""
    src = EDGE_FN.read_text()
    # two attempts with a +2s retry between them
    assert "attempt < 2; attempt++" in src, (
        "onboarding email must be attempted twice (0s/+2s)"
    )
    assert "setTimeout(r, 2_000)" in src, (
        "the retry must wait +2s before the second attempt"
    )
    # bounded (never blow the hook deadline)
    assert "AbortSignal.timeout(5_000)" in src, (
        "onboarding email fetch must be time-bounded"
    )
    # independent error handling inside the helper — a failure never throws
    # into the handler's outer catch (which would 500 a committed provision)
    assert 'console.error("Onboarding email failed:", e)' in src
    assert '"Onboarding email not sent: /internal/onboarding-email "' in src, (
        "non-definitive statuses must be logged"
    )


def test_edge_function_has_no_cors_surface():
    """#1120: ZERO CORS surface. No allowlist, no preflight answer, no
    `Access-Control-*` header on any response path.

    This inverts the #1111 guards on purpose. The preflight branch and the
    allowlist existed only for the browser caller that welcome.html used to be;
    a leftover allowlist is a surface to maintain (and a standing invitation
    for the next client to call this from a page), so its absence is the
    contract now. Do not "restore CORS" without re-opening #1120.
    """
    src = EDGE_FN.read_text()
    for forbidden in (
        "ALLOWED_ORIGINS",
        "ORIGIN_SUFFIXES",
        "LOCAL_ORIGIN_RE",
        "originAllowed",
        "Access-Control-Allow-Origin",
        "Access-Control-Allow-Methods",
        "Access-Control-Allow-Headers",
        "Access-Control-Max-Age",
    ):
        assert forbidden not in src, (
            f"{forbidden} must not exist — #1120 deleted the CORS surface "
            "(no allowlist to maintain, no preflight to answer, and no "
            "Access-Control-* header on any path)"
        )
    # An OPTIONS probe is a browser preflight and must NOT be answered as one:
    # it falls through to the plain method gate.
    assert 'req.method === "OPTIONS"' not in src, (
        "no OPTIONS/preflight branch may exist — a 405 with no CORS header is "
        "the whole answer a preflight gets"
    )
    assert 'req.method !== "POST"' in src, "the method gate must stay"


def test_edge_function_refuses_browser_origin_calls():
    """#1120: "no browser caller" is ENFORCED, not implied by absent headers.

    CORS headers only decide whether a browser can READ a response — the POST
    still executes. The dashboard BFF calls this server-side and strips
    `Origin`, so any request that carries one is a browser call and is refused.
    """
    src = EDGE_FN.read_text()
    assert 'req.headers.get("origin")' in src, (
        "the handler must read Origin to enforce the server-only invariant"
    )
    assert 'json({ error: "Forbidden: this endpoint is server-only" }, 403)' in src, (
        "an origin-bearing request must be refused 403 server-only"
    )
    # The refusal must precede authentication: a browser call is refused on the
    # caller's SHAPE, never after spending a token verification on it.
    # ⛔ Anchor on the REFUSAL, not on the header READ. `req.headers.get("origin")`
    # sits at the top of `Deno.serve`; comparing THAT index to
    # `authenticateCaller(req)` says nothing about where the 403 sits, so moving
    # the `if (requestOrigin) … 403` block BELOW the token verification left this
    # green (measured: 19/19 static tests passed with the refusal moved).
    assert src.index(
        'json({ error: "Forbidden: this endpoint is server-only" }, 403)'
    ) < src.index("authenticateCaller(req)"), (
        "the origin refusal itself must run before token verification — reading "
        "Origin early does not make the REFUSAL early"
    )


_CONSOLE_CALL_RE = re.compile(r"console\s*\.\s*(?:error|warn|log|info|debug)\s*\(")


def _console_call_arguments(code: str) -> str:
    """The ARGUMENTS of every `console.*(…)` call in `code`, concatenated.

    Why the monitor's literals are checked HERE and not against the whole file:
    the monitor greps `function_logs`, and Supabase fills that table with
    CONSOLE OUTPUT only — an HTTP response body is never a log line
    (`function_edge_logs` holds request/response *metadata*, not bodies). So a
    literal that exists only inside a `json({ error: … })` body can never
    satisfy a log grep.

    A plain `lit in code` substring test cannot tell those two apart, and it was
    MEASURED green over a live inert arm: the monitor greps
    `Provisioning misconfigured`, which appeared ONLY in the 500 response body,
    while the line actually logged said `FASTAPI_URL is not set`. That NEEDLE
    could never match, so the incident only ever fired off the monitor's
    `FASTAPI_URL is not set` needle — the SYMPTOM, not the condition — while the
    guard reported the marker healthy. That is the same class of defect the
    guard's own docstring says it exists to prevent, one literal further on.

    Balanced-paren and string-aware: an argument may be a concatenation
    (`"a" + x`), a template literal, or a nested call (`String(e)`), and a `)`
    inside a string must not close the span early. Callers strip comments first
    (`_strip_ts_comments`), so a `//` inside an argument cannot swallow the rest
    of the line.
    """
    spans: list[str] = []
    for match in _CONSOLE_CALL_RE.finditer(code):
        start = match.end()
        depth = 1
        i, n = start, len(code)
        quote: str | None = None
        while i < n and depth:
            ch = code[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in "\"'`":
                quote = ch
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        spans.append(code[start : i - 1 if depth == 0 else n])
    return "\n".join(spans)


def test_the_log_monitor_counts_only_literals_the_function_emits():
    """#1120: the monitor's counted literals must be things the function can
    actually LOG — otherwise the arm is an inert enforcer.

    This is not hypothetical: it is the must-fix that a review round filed against
    this very PR. The monitor counted `rejected origin not in allowlist`, emitted
    by the two CORS deny branches the PR deletes; after the deletion the count sat
    at 0 for ever while the workflow appeared to watch signup. Re-pointing the
    grep fixed that instance — this test pinning the COUPLING is what stops the
    next agent from re-breaking it by editing either file alone (the monitor greps
    a literal; the function is the only thing that emits it).

    Reads the workflow'S OWN greps rather than a copy of their text, so a marker
    added to the monitor is checked too.

    "THINGS THE FUNCTION CAN LOG" means a `console.*(...)` ARGUMENT — not any
    occurrence in the file. The weaker whole-file substring test certified a
    literal that existed only in a JSON response body, which `function_logs`
    can never contain (see `_console_call_arguments`), so the arm stayed dead
    while the guard reported healthy.
    """
    monitor = (
        _REPO_ROOT / ".github" / "workflows" / "tenant-provision-monitor.yml"
    ).read_text()
    counted = re.findall(r'grep -Fc "([^"]+)"', monitor)
    markers = re.findall(r'^\s+-e "([^"]+)"', monitor, re.MULTILINE)
    assert counted, "the monitor must count at least one literal (a count of nothing is not a guard)"
    assert markers, "the monitor must list at least one single-line marker"
    # CODE, not text: the function DOCUMENTS the counted literal in prose (it names
    # the monitor's grep in a comment), so a raw `in src` is satisfied with every
    # emit site deleted (review F2). `_strip_ts_comments` is this module's own
    # helper for exactly this hazard — the same reason the allowlist sweep uses it.
    # LOG SITES, not any code: a literal in a `json({…})` body is not a log line,
    # so it must not satisfy a grep that reads `function_logs` (the P2 this round).
    logged = _console_call_arguments(_strip_ts_comments(EDGE_FN.read_text()))
    for lit in counted + markers:
        assert lit in logged, (
            f"the monitor greps for {lit!r} but tenant-provision never LOGS it — "
            "an arm whose condition can never fire. The literal must appear inside a "
            "`console.error/warn/log/info/debug(...)` argument: Supabase's "
            "`function_logs` carries console output only, so a literal that exists "
            "only in a JSON response body can never satisfy a log grep. Log it, or "
            "point the monitor at a literal the function logs."
        )


def test_the_log_monitor_guard_separates_a_log_from_a_response_body():
    """The guard above is only as good as its log-vs-body split, so pin the SPLIT.

    A snippet test is the only place that can fail on the guard's own mechanism
    rather than on the function being scanned — the same reason
    `test_local_specifier_scan_reads_every_quote_style` exists. MEASURED on the
    pre-fix guard: a plain `lit in src` certified `Provisioning misconfigured`,
    which appeared ONLY in the 500 response body, so that NEEDLE could never
    match while the guard reported healthy.
    """
    snippet = (
        'console.error("tenant-provision: a REAL log marker:", detail);\n'
        "console.error(\n  \"multi-line \" + \"concatenated LOG marker\"\n);\n"
        'return json({ error: "a BODY-ONLY marker" }, 500);\n'
    )
    logged = _console_call_arguments(snippet)
    assert "a REAL log marker" in logged
    assert "concatenated LOG marker" in logged, (
        "a multi-line / concatenated console argument must still count as logged"
    )
    assert "a BODY-ONLY marker" not in logged, (
        "a literal that exists only in a response body must NOT satisfy the guard "
        "— Supabase's `function_logs` carries console output only"
    )
    # The pre-fix mechanism was a whole-text substring test, and it DID accept the
    # body-only literal — so this test pins a real difference, not a restatement.
    assert "a BODY-ONLY marker" in snippet


def test_edge_function_logs_the_refusal_on_the_method_gate():
    """#1120 (review F2): the refusal must be logged on the METHOD-GATE path too.

    A browser client must send `Authorization` + `Content-Type: application/json`,
    so it PREFLIGHTS first and its real request never leaves the browser. The
    preflight is answered by the method gate — before the origin gate — so a
    refusal logged only after the method gate means the monitor's count can never
    fire for the class it names ("a client regressed to calling from a page").
    """
    src = EDGE_FN.read_text()
    # BOUND THE SLICE AT THE METHOD GATE'S OWN BODY. Slicing to
    # `authenticateCaller(req)` also spans the ORIGIN GATE's log line, so the
    # assertion stayed GREEN with the method-gate log deleted — the exact
    # regression its docstring says it prevents (review F1). `res.headers.set(
    # "Allow", "POST")` is the last statement of the method-gate branch and occurs
    # exactly once in the file, so this endpoint includes the method-gate log and
    # excludes the origin gate's.
    gate = src[
        src.index('req.method !== "POST"')
        : src.index('res.headers.set("Allow", "POST")')
    ]
    assert "refused a browser-origin call" in gate, (
        "the method gate must log the browser-origin refusal (the monitor counts "
        "that literal, and a preflighted browser call never gets past this gate)"
    )


_LOCAL_SPECIFIER_RE = re.compile(
    r"""(?:from|import)\s*\(?(?:\s|//[^\n]*\n|/\*[^*]*\*+(?:[^/*][^*]*\*+)*/)*["'`](\.{1,2}/[^"'`]+)["'`]"""
)


def _local_specifiers(text: str) -> list[str]:
    """Every RELATIVE module specifier `text` names, in ANY quote style.

    Relative means the specifier starts with `./` or `../`; a URL
    (`https://esm.sh/…`) or a bare package specifier is not local, and this
    function's reachable code does not include it.

    Template literals count — `` import(`../_shared/x.ts`) `` is a specifier like
    any other — and so does a specifier that carries a comment between the
    keyword (or the `(`) and the literal, which is legal TypeScript:
    `import { x } from /* lazy */ "../_shared/x.ts"`. That class is spelled out
    here, with its own snippet test, because this pattern has now been WRONG
    three times in the same direction — a SPELLING it did not know:
      1. only `from "../_shared/…"` (missed `./_hook.ts`, single quotes, `import(`);
      2. still no backtick — MEASURED invisible (whole file green) against a full
         verifier in `../_shared/_hook.ts`, which the wholesale directory sweep
         cannot reach either;
      3. still no comment-interleaved spelling — MEASURED the same way (green
         with a live verifier in `../_shared/_hook.ts`), which is why the comment
         allowance is in the pattern rather than in a promise.

    ⛔ The comment branch is written in the CANONICAL LINEAR form
    (`/\\*[^*]*\\*+(?:[^/*][^*]*\\*+)*/`), not as a lazy `[\\s\\S]*?` inside the
    repetition: the lazy form is ambiguous — a comment's `*/` can either close
    the current comment or the outer `*` can end here and let the next `/*` start
    a new one — so a run of empty comments with no trailing literal exhausts the
    combinations. MEASURED on the lazy form, scanning `import /*a*//*a*/…` with no
    literal: 0.012 s at 16 repeats, 0.221 s at 20, 3.4 s at 24 — ×4 per two, so
    minutes by ~28. That is a denial-of-service on the guard itself, not a
    theoretical note; `test_the_specifier_pattern_cannot_backtrack_exponentially`
    holds the bound. The canonical form scans 2 000 such comments in 1 ms.
    The general lesson, since this is the third time: the walk is only as good as
    this pattern, so EVERY class the pattern is supposed to see has to appear in
    `test_local_specifier_scan_reads_every_quote_style` — a claim in a comment
    cannot fail, and each of the three rounds above was a comment that was true
    of the pattern's intent and false of its text.
    """
    return [match.group(1) for match in _LOCAL_SPECIFIER_RE.finditer(text)]


def test_local_specifier_scan_reads_every_quote_style():
    """The closure walk's front door, pinned on snippets.

    The walk below is only as good as this pattern, and this pattern has now
    been wrong three times in the same direction — a SPELLING it did not know. A
    snippet test is the only place that can fail on the pattern itself rather
    than on the function being scanned, so every class is asserted directly:
    three quote styles, dynamic `import(`, a multi-line `from`, a re-export, and
    a specifier that carries a `/* … */` or `// …` comment between the keyword
    and the literal (each of those three was a measured GREEN-with-a-live-
    verifier hole before it was added here).
    """
    text = "\n".join(
        [
            'import { a } from "./_a.ts";',
            "import { b } from '../_shared/b.ts';",
            "const c = await import(`../_shared/c.ts`);",
            'const d = await import("../_shared/d.ts");',
            'import {\n  e,\n} from "../_shared/e.ts";',
            'export * from "../_shared/f.ts";',
            'import { g } from /* lazy */ "../_shared/g.ts";',
            'const h = await import(/* lazy */ "../_shared/h.ts");',
            'import {\n  i,\n} from\n  // lazy\n  "../_shared/i.ts";',
            'const j = await import(\n  /* lazy */\n  "../_shared/j.ts"\n);',
            'import "https://esm.sh/@supabase/supabase-js@2";',
            'import { z } from "npm:zod@3";',
        ]
    )
    assert _local_specifiers(text) == [
        "./_a.ts",
        "../_shared/b.ts",
        "../_shared/c.ts",
        "../_shared/d.ts",
        "../_shared/e.ts",
        "../_shared/f.ts",
        "../_shared/g.ts",
        "../_shared/h.ts",
        "../_shared/i.ts",
        "../_shared/j.ts",
    ], (
        "every relative specifier must be seen whatever the quote style AND "
        "whatever comment sits between the keyword and the literal — a missed "
        "specifier is a module the closure walk never reads, while the pins "
        "still report green"
    )


def test_the_specifier_pattern_cannot_backtrack_exponentially():
    """The pattern must be LINEAR in the comment gap, not merely correct.

    A lazy `[\\s\\S]*?` inside the repetition is ambiguous — the same `*/` can close
    a comment or terminate the gap so the next `/*` opens one — so a run of empty
    comments with NO trailing literal makes the engine try every split. Measured
    on that form: 0.012 s at 16 repeats, 0.221 s at 20, 3.4 s at 24 (×4 per two).
    The guard runs on repository content, so a pattern that can hang on a comment
    is a denial of service against every CI run that touches this file.

    24 repeats is chosen deliberately: the ambiguous form needs ~3.4 s there while
    the canonical form needs microseconds, so the bound below has five orders of
    magnitude of headroom and still FAILS (rather than hanging) on a regression.
    """
    text = "import " + "/*a*/" * 24
    started = time.perf_counter()
    assert _local_specifiers(text) == []
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, (
        "the specifier pattern backtracked exponentially on a comment run "
        f"({elapsed:.3f}s for 24 empty comments) — it must stay linear, see the "
        "canonical comment form in `_local_specifiers`' docstring"
    )


# ── The deleted `after_user_created` hook path's REQUEST-SHAPED fingerprints ──
# Matched on the METHOD and for ANY receiver name, never on the identifier `req`.
# That is not cosmetic: a verifier restored into a helper defined OUTSIDE the
# handler CANNOT name its parameter `req` — that identifier is the handler's own
# local, out of scope in a top-level function — so a `req.`-spelled pin covers
# only the restore shape that reuses the handler's name. MEASURED at this head
# (PR #11553 review):
#
#     const _hdr = (r: Request) => { const h: Record<string, string> = {};
#       r.headers.forEach((v, k) => { h[k] = v; }); return h; };
#
# inserted above `authenticateCaller` left `test_edge_function_hook_path_is_gone`
# GREEN while the pins read `req.headers.forEach`, and reds under this table —
# which is why the shape lives here as data, so the snippet test below can hold
# it to the classes it must see.
_HOOK_REQUEST_SHAPED: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\.headers\.forEach\s*\("),
        "header enumeration (.headers.forEach on ANY receiver)",
    ),
    (
        re.compile(r"\.headers\.entries\s*\("),
        "header enumeration (.headers.entries on ANY receiver)",
    ),
    (
        re.compile(r"fromEntries\s*\(\s*[\w$]+\s*\.\s*headers"),
        "header enumeration (Object.fromEntries(<any>.headers))",
    ),
)


def test_edge_function_hook_path_is_gone():
    """#1120: the `after_user_created` auth-hook path (AUTH_HOOK_SECRET /
    Standard-Webhooks) is DELETED, not merely disabled.

    It cannot be re-enabled: `after_user_created` is not a supported Supabase
    auth hook (the docs list Before User Created, Custom Access Token, Send
    SMS, Send Email, MFA Verification Attempt, Password Verification
    Attempt). #839's 500 was a platform-capability gap, so keeping a
    fail-closed, unverifiable branch alive only invites someone to
    "re-enable" a path the platform can never call.

    WHY A SHAPE PIN AND NOT ONLY THE NAMES BELOW: the two name assertions are
    satisfiable by a WORKING re-add — one that spells the secret as a plain
    literal (`Deno.env.get("AUTH_HOOK_SECRET")`) instead of the deleted
    `AUTH_HOOK_SECRET_ENV` constant needs no `standardwebhooks` import to read a
    `webhook-signature` header, and a REAL verifier rejects a bogus signature
    (401), which is exactly what the runtime harness asserts. Measured on such a
    mutant (PR #11553 review): every name pin passed. So the delete claim is
    pinned on the SHAPE too — `authenticateCaller` reads exactly ONE request
    header, `Authorization`; any second header read is an authentication path
    beside the user JWT.
    """
    src = EDGE_FN.read_text()
    assert "standardwebhooks" not in src, (
        "the Standard-Webhooks import must be gone with the hook path"
    )
    assert "AUTH_HOOK_SECRET_ENV" not in src, (
        "the hook-secret constant must be gone with the hook path"
    )
    # A hook signature is no longer an authentication path: the ONLY path is a
    # user JWT, whose identity must match the provisioning target (#802).
    assert "authenticateCaller(req)" in src, (
        "the JWT path is the only caller authentication left"
    )
    auth_body = src.split("async function authenticateCaller", 1)[1].split(
        "Deno.serve", 1
    )[0]
    # Pin the SHAPE of a header/body read, not one call spelling. The deleted
    # hook path gathered headers with `req.headers.forEach((v, k) => …)` and read
    # the raw body for the signature — neither is a `req.headers.get(` call:
    #
    #   // Headers → plain object (webhook-signature / webhook-id / ...)
    #   const headers: Record<string, string> = {};
    #   req.headers.forEach((v, k) => { headers[k] = v; });
    #   const payload = webhook.verify(rawBody, headers) as HookPayload;
    #
    # (origin/main:supabase/functions/tenant-provision/index.ts:184-187).
    # MEASURED: re-inserting exactly that branch into authenticateCaller — with
    # a plain `Deno.env.get("AUTH_HOOK_SECRET")` literal, so the name pins stay
    # green — kept every other static test AND the runtime harness passing while
    # the branch was live and would authenticate a real `v1,whsec_…` secret.
    assert auth_body.count("req.headers.get(") == 1, (
        "authenticateCaller must read exactly ONE request header (Authorization): "
        "a second header read is an auth path beside the user JWT — e.g. the "
        "deleted Standard-Webhooks `webhook-signature` path, which a "
        "plain-literal re-add reintroduces while the name pins above stay green:"
        f"\n{auth_body}"
    )
    assert "req.headers.forEach" not in auth_body, (
        "authenticateCaller must not ENUMERATE request headers: gathering them is "
        "how the deleted hook path collected `webhook-signature` without ever "
        "calling req.headers.get(, so the count above cannot see it:"
        f"\n{auth_body}"
    )
    assert "req.clone" not in auth_body, (
        "authenticateCaller must not clone the request: a signature-verifying "
        "hook path re-reads the RAW BODY, so a clone here is the fingerprint of "
        "one — the live code reads it ONCE, in the handler, and passes it in "
        "(`origin/main` `:238`, `:242`):"
        f"\n{auth_body}"
    )
    # ⛔ AND THE SAME PINS FILE-WIDE, because `auth_body` is a SOURCE SLICE and a
    # restore can step outside it. MEASURED (this PR's review): moving the hook
    # path into a helper defined ABOVE `async function authenticateCaller` and
    # calling it from inside — one benign-looking line — left ALL 19 static
    # tests green while the branch was a live authentication path. The slice
    # cannot see a function it does not contain, so the artifacts of a webhook
    # signature path are pinned across the WHOLE file and its import closure — as
    # a SHAPE, never as a count, because a count that must track a growing file is
    # a self-inflicted staleness:
    #   .headers.forEach / .headers.entries / Object.fromEntries(x.headers)
    #                        — header enumeration, matched on the METHOD and for
    #                          ANY receiver name (`_HOOK_REQUEST_SHAPED`), not on
    #                          the identifier `req`. A helper defined OUTSIDE the
    #                          handler cannot reuse `req` — that identifier is the
    #                          handler's local — so a `req.`-spelled pin covers only
    #                          the restore shape that keeps the same name. MEASURED
    #                          at this head: `const _hdr = (r: Request) => {
    #                          r.headers.forEach(…) }` above `authenticateCaller`
    #                          — the natural way to write that helper — left the
    #                          `req.`-spelled version of this pin GREEN, which is
    #                          the very hole the closure sweep exists to close.
    #                          `test_the_hook_fingerprint_ignores_the_request_
    #                          parameter_name` pins that separation on a snippet.
    #   req.clone            — a SECOND body read (the live handler reads it once,
    #                          with `req.text()`, and passes it in as an argument).
    #                          Still name-bound, and deliberately so: a restore that
    #                          renames the receiver STILL has to read the
    #                          `webhook-signature` header to verify anything, so the
    #                          enumeration pins above (or the literal name pin
    #                          below) catch it — this pin is redundancy, not the
    #                          only door.
    #   webhook-id / webhook-timestamp / webhook-signature — the protocol's own
    #                          header names, LITERALLY SPELLED
    #   createHmac           — the signature computation
    # WHAT THESE PINS DO **NOT** COVER — stated precisely, because two earlier
    # revisions of this comment were WRONG about it (the first claimed the header
    # names were a backstop for `crypto.subtle`; the second claimed the runtime
    # harness caught that mutant. Neither reproduced):
    #   (a) a verifier that CONSTRUCTS the header name rather than spelling it
    #       (`req.headers.get("webhook-" + suffix)`), AND
    #   (b) an HMAC computed through `crypto.subtle` — which this file uses
    #       legitimately (3 sites: the org-id digest, and the API-key hash's
    #       `importKey`/`deriveBits`), so it cannot be pinned by absence, AND
    #   (c) a NEW env var name (`SUPABASE_AUTH_HOOK_SECRET`), which no name pin
    #       predicts.
    #   MEASURED at this head, all three together in a helper above
    #   authenticateCaller: these static pins PASS (the WHOLE file, not a subset —
    #   deliberately no count here, because a count that must track a growing file
    #   is a self-inflicted staleness: this paragraph said "19 passed" and was
    #   false one commit later) and the runtime harness ALSO PASSES (20/20) —
    #   because the harness configures `AUTH_HOOK_SECRET`, so a branch keyed on a
    #   different name is inert under test. A restore reusing the OLD env name IS
    #   caught by the Phase-2 arms (they assert 401 and get the hook path's
    #   non-401); one that invents a name is not caught by either side.
    #   THAT RESIDUAL IS BOUNDED BY A DIFFERENT SURFACE, not by a test here, and
    #   by exactly ONE surface: the branch cannot authenticate in production
    #   unless that secret is actually provisioned, and NOTHING IN THIS REPO
    #   GATES THAT. Measured, because an earlier revision of this paragraph
    #   named two gates that do not bind:
    #     · `supabase-deploy.yml`'s required-secret check verifies exactly
    #       FASTAPI_URL / FASTAPI_INTERNAL_KEY / TORTOISE_SECRET_PEPPER (its own
    #       `required=(...)` line), and THIS SAME PR's comment above it states
    #       AUTH_HOOK_SECRET is "deliberately NOT required" — so the deploy
    #       passes with a hook secret absent, and the step is dispatch-only;
    #     · `tenant-provision-monitor.yml` counts `refused a browser-origin call`
    #       (emitted ONLY when an `Origin` header is present) and pages on
    #       `FASTAPI_URL is not set` / `Provisioning misconfigured` /
    #       `provision_team failed` / `tenant-provision error` — a restored
    #       verifier is server-to-server and can provision successfully, so it
    #       emits none of them.
    #   The bound that DOES hold is the OPERATOR one (the sentence at the end of
    #   this paragraph — not the deploy bullet above, which is the disproof):
    #   `supabase secrets set` is an out-of-band operator action, so the secret
    #   is absent unless a human provisions THAT NAME, and its absence FAILS
    #   CLOSED. Both halves of that sentence carry conditions, stated here because
    #   an unqualified bound is the same defect one round further on:
    #     · THAT NAME — a restore reusing `TORTOISE_SECRET_PEPPER` instead of a
    #       new name would be handed a secret the deploy check GUARANTEES exists,
    #       so nothing bounds it at all;
    #     · FAILS CLOSED is a property of the code that was DELETED (its copy of
    #       this branch returned null on an absent secret), not of whatever a
    #       future restorer writes — a verifier that treats absence as "skip"
    #       fails OPEN, and no test in this repo can pin code that does not exist.
    #   No test in THIS file pins the deployment's secret inventory, and this
    #   comment claims no gate.
    #   These pins are therefore defence in depth over the literal and idiomatic
    #   spellings of a restore — useful, cheap, and NOT claimed to be complete.
    # ⛔ AND OVER THE FUNCTION'S REACHABLE CODE, not just this file, because a
    # restore can step outside BOTH the slice and the file. MEASURED (this PR's
    # review): putting the verifier in `../_shared/<new>.ts`, importing it and
    # calling `hookIdentity(req)` from the handler left EVERY static test in the
    # file green at that head (19 of them then — the count is not restated as a
    # current figure, since the file grows and this sentence measures a moment)
    # — `index.ts` then carries no banned token at all, and the helper enumerates
    # headers in the other file. The runtime harness only trips it incidentally
    # (its import-rewriting cannot resolve the new module, so it dies with
    # ERR_MODULE_NOT_FOUND rather than failing an assertion), which is not a gate.
    # A SECOND round showed the fix for that was too narrow: matching only
    # `from "../_shared/…"` missed a SIBLING module (`./_hook.ts`), single quotes,
    # `../other-fn/x.ts` and dynamic `import(`, so a verifier in
    # `tenant-provision/_hook.ts` stayed invisible. A THIRD round found the
    # pattern still missed a TEMPLATE LITERAL (`` import(`../_shared/x.ts`) ``),
    # which is outside the swept directory and was measured invisible (19
    # passed) against a full verifier. The closure is therefore derived from
    # EVERY relative specifier in all three quote styles, via
    # `_local_specifiers()` — which carries its own snippet test, because the
    # quote class is exactly what was wrong twice — resolved TRANSITIVELY, and
    # the function's own directory is swept wholesale so a module the pattern
    # still misses cannot be left unscanned:
    closure: list[tuple[str, str]] = []
    seen: set[Path] = set()
    queue: list[Path] = [EDGE_FN]
    while queue:
        path = queue.pop()
        real = path.resolve()
        if real in seen:
            continue
        seen.add(real)
        text = real.read_text()
        closure.append((str(real.relative_to(_REPO_ROOT)), text))
        for rel in _local_specifiers(text):
            dep = (real.parent / rel).resolve()
            assert dep.is_file(), (
                f"{real.name} imports a missing local module: {rel} — a rename "
                "must not silently shrink the scanned set"
            )
            queue.append(dep)
    # Belt and braces: every module in the function's own directory, whether or
    # not a specifier pointed at it.
    for sibling in sorted(EDGE_FN.parent.rglob("*.ts")):
        if sibling.resolve() not in seen:
            seen.add(sibling.resolve())
            closure.append(
                (
                    str(sibling.resolve().relative_to(_REPO_ROOT)),
                    sibling.read_text(),
                )
            )
    assert len(closure) >= 2, (
        "the scan must cover more than index.ts — an empty/one-file closure means "
        "the specifier walk found nothing, which would silently restore the hole"
    )
    for rel, text in closure:
        for banned in (
            "req.clone",
            "webhook-id",
            "webhook-timestamp",
            "webhook-signature",
            "createHmac",
        ):
            assert banned not in text, (
                f"the deleted hook path's fingerprint `{banned}` must not appear "
                f"ANYWHERE in the code this function runs (`{rel}`) — not just "
                "outside authenticateCaller's body, and not just outside "
                "index.ts, because a helper in either place reaches the same "
                "request and the slice-based pins above cannot see it"
            )
        for pattern, what in _HOOK_REQUEST_SHAPED:
            match = pattern.search(text)
            assert match is None, (
                f"the deleted hook path's fingerprint `{what}` must not appear "
                f"ANYWHERE in the code this function runs (`{rel}`) — matched by "
                f"SHAPE ({pattern.pattern}) on any receiver name, because a helper "
                "defined outside the handler cannot name its parameter `req` and a "
                "name-spelled pin cannot see it. Offending text: "
                f"`{match.group(0) if match else ''}`"
            )
    assert 'req.headers.get("authorization")' in auth_body, (
        "the one header authenticateCaller reads must be Authorization"
    )
    # ⛔ And no SECRET is read in there either — as an ALLOW-LIST of env names,
    # not a name. The pins above look for the deleted `AUTH_HOOK_SECRET_ENV`
    # CONSTANT, so a plain-literal re-add (`Deno.env.get("AUTH_HOOK_SECRET")`) is
    # invisible to them; this is the pin that sees it. It replaces a
    # strictly-subsumed line (`assert "Deno.env.get(AUTH_HOOK_SECRET_ENV)" not in
    # src` — a string that CONTAINS the name the assertion above already rejects,
    # so it could not fail on its own; PR #11553 review, ISSUE 2). A SUBSET, not
    # an equality, so extracting the client helper is not a false block; the
    # auth path actually executing is the runtime harness's job, not this pin's.
    # Non-vacuous: inserting that literal into authenticateCaller reds here.
    auth_envs = set(re.findall(r'Deno\.env\.get\("([A-Z0-9_]+)"\)', auth_body))
    assert auth_envs <= {"SUPABASE_URL", "SUPABASE_ANON_KEY"}, (
        "authenticateCaller may read only the Supabase client's two env vars "
        "(SUPABASE_URL, SUPABASE_ANON_KEY) — any other is a secret-reading "
        "authentication path beside the user JWT, which is what the deleted "
        "Standard-Webhooks branch was: "
        f"{sorted(auth_envs - {'SUPABASE_URL', 'SUPABASE_ANON_KEY'})}"
    )


def test_the_hook_fingerprint_ignores_the_request_parameter_name():
    """Non-vacuity for `_HOOK_REQUEST_SHAPED`: it must see a header ENUMERATION
    whatever the receiver is called, and must still separate a benign single
    header read from one.

    This is the snippet pin for the reason the pattern is receiver-agnostic. The
    first sample is the shape MEASURED at this head: a helper above
    `authenticateCaller` cannot use `req` (the handler's local), so the natural
    spelling is `(r: Request) => r.headers.forEach(…)` — and it kept the
    `req.`-spelled pin GREEN. Every sample below therefore deliberately contains
    NO `req.`, and the last block asserts that, so the samples cannot quietly
    become shapes the old pin would also have caught.
    """
    samples = [
        # the natural helper form — a renamed receiver, forEach
        "const _hdr = (r: Request) => { const h: Record<string, string> = {}; "
        "r.headers.forEach((v, k) => { h[k] = v; }); return h; };",
        # the iterator form, which the deleted path could equally have used
        "for (const [k, v] of request.headers.entries()) { hdrs[k] = v; }",
        # the one-line form with a destructured/gathered parameter
        "const headers = Object.fromEntries(inner . headers);",
    ]
    for sample in samples:
        assert "req." not in sample, (
            "each sample must be free of `req.`, or it would pass under the "
            "name-spelled pin this test exists to prove is not sufficient: "
            f"{sample}"
        )
        matched = [what for pattern, what in _HOOK_REQUEST_SHAPED if pattern.search(sample)]
        assert matched, (
            "a header enumeration on a receiver that is NOT named `req` must be "
            "caught — otherwise a restored hook verifier in a helper above the "
            "handler is invisible to the guard: " + sample
        )
    # …and it must still SEPARATE the benign shape from those: the live code's
    # one header read is a `get`, which no enumeration pin may match.
    benign = 'const authz = req.headers.get("authorization") ?? "";'
    assert not [p for p, _ in _HOOK_REQUEST_SHAPED if p.search(benign)], (
        "the enumeration pins must not match a single header read — an "
        "over-matching pin would red the live code and train the next author to "
        "delete it rather than fix it"
    )


def test_edge_function_declares_no_origin_allowlist():
    """#1120: tenant-provision is the one HTTP-facing function with NO browser
    surface, so its absence of an allowlist must be an EXPLICIT, CHECKED entry in
    `SERVER_ONLY_FUNCTIONS` — not the accident of a sweep that happens to find
    nothing.

    This reads the set the parity sweep compares against, which is what stops it
    being a restatement of `test_edge_function_has_no_cors_surface`: dropping
    `tenant-provision` from `SERVER_ONLY_FUNCTIONS` (the way a future author would
    "fix" the sweep) reds HERE and in the sweep, and re-growing an allowlist reds
    the CORS test.
    """
    assert EDGE_FN.parent.name in SERVER_ONLY_FUNCTIONS, (
        f"{EDGE_FN.parent.name} must be listed in SERVER_ONLY_FUNCTIONS — the "
        "sweep's allowance for a function with no browser surface is granted "
        "here, by name, not inferred from a missing declaration"
    )


def _allowlist_pattern(const: str) -> re.Pattern[str]:
    # Anchors the declaration and captures its opening bracket. The BODY is taken
    # by `_array_body`, not by a regex: `\[([^\]]*)\]` stops at the first `]` in
    # the file, so a `]` inside a host string (an IPv6 origin such as
    # `https://[::1]:1`) truncated the compared value and hid every host after it
    # — measured: a drifted host placed after such an entry passed. The optional
    # opening paren accepts a parenthesised literal (`= ([...] as const)`), which
    # is a literal the sweep must compare rather than report as absent.
    return re.compile(
        rf"(?:export\s+)?(?:const|let|var)\s+{const}\s*(?::[^=\n]+)?=\s*\(?\s*(\[)"
    )


def _array_body(code: str, open_idx: int) -> str | None:
    """The text between the `[` at `open_idx` and its matching `]`.

    String-aware and depth-aware: a `]` inside a quoted host does not end the
    array, and an array of arrays is captured whole. `None` when the bracket is
    never closed — the caller then has a declaration it cannot read, which is a
    RED, not a silence.
    """
    if open_idx >= len(code) or code[open_idx] != "[":
        return None
    depth = 0
    quote: str | None = None
    i = open_idx
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return code[open_idx + 1:i]
        i += 1
    return None


def _normalise_array_body(body: str) -> str:
    """The body's elements, with whitespace and separators folded away.

    Commas carry no meaning for a comparison of hosts, and folding them matters:
    a comment removed from inside the array leaves its separating comma behind, so
    comparing raw bodies would make a comment-only edit to one file look like
    drift. A comma inside a host string is folded on both sides alike, so the
    comparison is unaffected.
    """
    return re.sub(r"[\s,]+", " ", body).strip()


def _allowlist_arrays(code: str, const: str) -> list[str]:
    """Every array-literal body declared for `const`, normalised.

    `code` must already be comment-stripped: the compared values ARE URLs, so a
    comment removed by regex would collapse them.
    """
    pat = _allowlist_pattern(const)
    bodies = []
    for match in pat.finditer(code):
        body = _array_body(code, match.start(1))
        if body is not None:
            bodies.append(_normalise_array_body(body))
    return bodies

def _strip_ts_comments(text: str) -> str:
    """The file's code, with block and line comments removed.

    String-aware on purpose: the values being compared ARE URLs, so removing
    every `//` to end-of-line would collapse all four hosts to `"https:` and
    make three drifted files compare EQUAL (measured). Quotes are tracked, so
    `//` inside a string survives and only real comments go. Comments are
    removed rather than skipped so a literal inside a comment can neither
    satisfy the comparison nor break it, and a declaration sharing a line with
    a block comment is still examined.
    """
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if text.startswith("//", i):
            nxt = text.find("\n", i)
            i = n if nxt < 0 else nxt
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)

def _unreadable_allowlist_declarations(code: str, const: str) -> list[str]:
    """A declaration of `const` whose right-hand side is not an array literal.

    Reading literals alone cannot see a wrapped or imported value, so a file
    could serve `Object.freeze([...])` or `makeOrigins()` while a commented
    literal satisfied the comparison — measured GREEN with a comment copy of
    the reference beside a served non-literal. A declaration the sweep cannot
    read is a RED, not a gap.

    Comments are stripped, not skipped, and the search spans the whole text:
    skipping a line left `/* <reference> */ const NAME = makeOrigins();` on
    one line entirely unexamined (measured GREEN, served value a call), while
    deciding per line made `const NAME =` followed by the array on the NEXT
    line a false RED whose remedy ("make it an array literal") was already
    satisfied. Stripping spans and searching the whole text handles both, and
    still clears the two false REDs that a raw-text match produced: a
    commented `// let NAME = makeOrigins();` and a JSDoc `@example`. A
    parenthesised literal (`= ([...] as const)`) and one broken after `=` are
    both read as the literals they are.
    """
    pat = re.compile(
        rf"(?:export\s+)?(?:const|let|var)\s+{const}\s*(?::[^=\n]+)?=(?!\s*\(*\s*\[)"
    )
    out: list[str] = []
    for hit in pat.finditer(code):
        fragment = " ".join(code[hit.start():hit.end() + 40].split())
        out.append(fragment[:80])
    return out


def test_allowlist_declaration_scan_reads_code_not_text():
    """The scan's contract, pinned on snippets.

    Three review cycles found this helper wrong in three different ways: it read
    only the FIRST declaration (a decoy could stand in for a drifted array), then
    it matched raw text (a commented example became a false RED while a decoy
    satisfied the comparison), then it decided per line (a literal whose bracket
    wrapped to the next line was refused, and a declaration sharing its line with
    a block comment was never examined). Each case below is one of those.
    """
    ref = _allowlist_pattern("ALLOWED_ORIGINS")
    literal = 'const ALLOWED_ORIGINS = [\n  "https://premiselabs.co",\n];'
    assert ref.search(literal), "a literal declaration must be read"
    # The compared values ARE URLs: comment stripping must not touch a `//` that
    # lives inside a string, or every host collapses and drifted files compare equal.
    assert _strip_ts_comments(literal).count("https://premiselabs.co") == 1
    assert not _unreadable_allowlist_declarations(
        _strip_ts_comments(literal), "ALLOWED_ORIGINS"
    )
    # A declaration the scan cannot READ is a RED, wherever the decoy sits.
    for bad in (
        "const ALLOWED_ORIGINS = makeOrigins();",
        "const ALLOWED_ORIGINS = Object.freeze([\n  \"https://a\",\n]);",
        '/* const ALLOWED_ORIGINS = ["https://a"]; */ const ALLOWED_ORIGINS = makeOrigins();',
    ):
        assert _unreadable_allowlist_declarations(
            _strip_ts_comments(bad), "ALLOWED_ORIGINS"
        ), bad
    # Neither a comment nor a line break may be read as a fault.
    for good in (
        "// let ALLOWED_ORIGINS = makeOrigins();\n" + literal,
        "/** @example const ALLOWED_ORIGINS = build(); */\n" + literal,
        "// previously: [\"https://old.example\"]\n" + literal,
        'const ALLOWED_ORIGINS =\n  ["https://a"];',
        'const ALLOWED_ORIGINS = (["https://a"] as const);',
        'const ALLOWED_ORIGINS: string[] = ["https://a"];',
    ):
        code = _strip_ts_comments(good)
        assert not _unreadable_allowlist_declarations(code, "ALLOWED_ORIGINS"), good
        assert ref.search(code), good
    # The captured VALUE must be the whole array, not the prefix before the first
    # `]` in the file. A `]` inside a quoted host (an IPv6 origin) or in a nested
    # array used to truncate it — measured: at `[^\]]*` a drift placed after such
    # an entry passed the parity check while the served value had changed.
    for src, expected in (
        (
            'const ALLOWED_ORIGINS = ["https://[::1]:1", "https://tortoise.premiselabs.co"];',
            '"https://[::1]:1" "https://tortoise.premiselabs.co"',
        ),
        (
            'const ALLOWED_ORIGINS = [["https://a"], ["https://b"]];',
            '["https://a"] ["https://b"]',
        ),
        (
            'const ALLOWED_ORIGINS = ["https://a//b", "https://c"];',
            '"https://a//b" "https://c"',
        ),
    ):
        stripped = _strip_ts_comments(src)
        assert _allowlist_arrays(stripped, "ALLOWED_ORIGINS") == [expected], src
    # An unterminated array is a declaration the scan cannot read, never a silence.
    assert (
        _allowlist_arrays('const ALLOWED_ORIGINS = ["https://a";', "ALLOWED_ORIGINS")
        == []
    )
    # A comment inside the REFERENCE array must not red an honest change either:
    # the reference is stripped like every scanned file — and the comma its removal
    # leaves behind must not be read as drift by the comparison.
    ref_src = 'const ALLOWED_ORIGINS = [\n  "https://a", // product host\n];'
    assert _allowlist_arrays(_strip_ts_comments(ref_src), "ALLOWED_ORIGINS") == [
        '"https://a"'
    ]


def test_edge_function_allowlist_parity_with_waitlist_subscribe():
    """EVERY function that declares an allowlist must agree with
    waitlist-subscribe (the reference array) so a host added for one browser
    surface can never silently drift from the others (the #1110 failure mode).
    Local-origin handling is NOT compared — and since #1120 there is nothing
    left to diverge: tenant-provision declares NO allowlist (it is in
    SERVER_ONLY_FUNCTIONS), and the remaining declarers share the 8788-only
    array.

    Enumerated rather than named: the guard originally compared two functions,
    and a third (deck-view, #8732) joined them unguarded. Its failure mode is
    fail-SHUT — the gate answers 403 and the deck stays locked — so a host
    added to the guarded pair while deck-view drifts would lock readers out with
    nothing red.
    """
    # ONE pattern per constant does both jobs — finding the declaring files and
    # reading their values — so the enumerated set and the compared set cannot
    # diverge. When they were separate (`decl` accepting `const|let|var`,
    # `declared_lists` parsing only `const NAME = [`), a `let ALLOWED_ORIGINS =
    # [...]` was enumerated but not parsed: a comment decoy satisfied the
    # comparison while the served array rode free (measured: a comment decoy
    # above a `let` missing `app.premiselabs.co` passed). An optional type
    # annotation is accepted so a typed declaration is compared, not reported as
    # absent. Enumerated by FUNCTION, not by the files that happen to contain the
    # literal: a sweep keyed on the literal would drop a file the moment the
    # constant is renamed — and dropping it is LOUD (the `missing` check fires),
    # which is the point. Every HTTP-facing function must declare an allowlist —
    # EXCEPT the ones with no browser surface, which are named in SERVER_ONLY
    # below and whose absence is checked EXACTLY. The glob covers the extensions
    # Supabase accepts, not just `.ts`.

    # #1120: functions with NO browser caller declare NO allowlist, and this set
    # is compared, not skipped. Two drifts it catches that a `continue` would
    # not: a NEW function with no allowlist (it lands in `missing` and reds —
    # it may be a browser surface someone forgot to scope), and a member of this
    # set RE-declaring one (it leaves `missing`, so the sets differ — a
    # server-only function carrying a CORS surface is exactly the drift #1120
    # removed). `_shared` is imported, never served.
    SERVER_ONLY = SERVER_ONLY_FUNCTIONS

    decl = _allowlist_pattern("ALLOWED_ORIGINS")
    declaring: dict[str, str] = {}
    missing: list[str] = []
    unreadable: list[str] = []
    functions_dir = _REPO_ROOT / "supabase" / "functions"
    for fn_dir in sorted(p for p in functions_dir.iterdir() if p.is_dir()):
        files = [
            f for f in sorted(fn_dir.rglob("*"))
            if f.suffix in {".ts", ".tsx", ".mts", ".cts"}
        ]
        # Compare CODE, not text: a literal sitting inside a comment is neither a
        # declaration nor a reason to red (a comment documenting an older allowlist
        # must not fail the guard), and a declaration sharing a line with a block
        # comment must still be examined.
        code = {f: _strip_ts_comments(f.read_text()) for f in files}
        hits = [f for f in files if decl.search(code[f])]
        wrapped = [f for f in files if _unreadable_allowlist_declarations(code[f], "ALLOWED_ORIGINS")]
        if not hits and not wrapped:
            missing.append(fn_dir.name)
        unreadable.extend(
            f"{fn_dir.name}/{f.relative_to(fn_dir)}" for f in wrapped
        )
        for hit in hits:
            declaring[f"{fn_dir.name}/{hit.relative_to(fn_dir)}"] = code[hit]
    # The specific diagnosis first: a declaration the sweep cannot READ is not an
    # absent one, and the two need different remedies.
    assert not unreadable, (
        f"these declarations are not array literals: {unreadable} — the sweep "
        "reads literals, so a wrapped or imported allowlist cannot be compared "
        "(and a commented literal beside it would pass while the served value "
        "drifted). Declare it inline as a literal array, or extend the sweep."
    )
    assert sorted(missing) == sorted(SERVER_ONLY), (
        f"these functions declare no ALLOWED_ORIGINS: {sorted(missing)} — the "
        f"server-only set is {sorted(SERVER_ONLY)}. An HTTP-facing function "
        "needs an allowlist; a function that has NO browser surface belongs in "
        "SERVER_ONLY with a reason, not in a `continue`. The two sets are "
        "compared EXACTLY so both directions red: a new function that declares "
        "nothing, and a SERVER_ONLY function that re-grows a CORS surface. If a "
        "declaration cannot be READ that is the other assertion — do not paper "
        "over it here."
    )
    assert len(declaring) >= 2, f"allowlist sweep found only {sorted(declaring)}"
    # The reference is stripped too: it is the anchor for every comparison, so a
    # comment in it that mentions the constant must not red an honest change —
    # measured, a trailing `// product host` inside the reference array failed the
    # guard and blamed deck-view, a file nobody had touched.
    waitlist = _strip_ts_comments(
        (
            _REPO_ROOT / "supabase" / "functions" / "waitlist-subscribe" / "handle.ts"
        ).read_text()
    )
    for const in ("ALLOWED_ORIGINS", "ORIGIN_SUFFIXES"):
        want = _allowlist_arrays(waitlist, const)
        assert len(want) == 1, f"the reference must declare {const} exactly once"
        for name, src in sorted(declaring.items()):
            have = _allowlist_arrays(src, const)
            assert have, (
                f"{name} declares no {const} the sweep can read — a declaration "
                "the sweep cannot parse is a RED, not a gap: declare it inline as "
                "an array literal, or extend the sweep. Do not skip the file."
            )
            assert all(h == want[0] for h in have), (
                f"{name} {const} must match waitlist-subscribe in EVERY "
                "declaration — change every DECLARING function together "
                "(tenant-provision is excluded by SERVER_ONLY_FUNCTIONS, not by "
                "an allowlist it no longer has)"
            )


def test_edge_function_only_caller_is_the_same_origin_bff():
    """#1120: the dashboard's same-origin BFF is the ONLY caller of this
    function, and it STRIPS `Origin` before forwarding — which is what makes the
    server-only 403 in the handler correct rather than hostile to the live path.

    Both halves are pinned together on purpose: the 403 is only safe while the
    BFF keeps stripping `Origin`, and the browser only stays off the function
    while the dashboard calls `/api/provision`. Either one drifting alone breaks
    signup — silently, since nothing else reaches this endpoint.
    """
    bff = (
        _REPO_ROOT / "website" / "apps" / "dashboard" / "functions" / "api" / "provision.ts"
    ).read_text()
    assert 'const PROVISION_PATH = "/functions/v1/tenant-provision";' in bff, (
        "the BFF must be the function's caller"
    )
    strip_block = bff.split("const STRIP_REQUEST", 1)[1].split("]", 1)[0]
    assert '"origin",' in strip_block, (
        "the BFF must keep stripping Origin — the handler refuses an "
        "origin-bearing request 403 server-only"
    )
    # No browser-side caller: the dashboard's own source must never address the
    # Edge Function directly (it posts to the same-origin `/provision` route).
    main = (
        _REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"
    ).read_text()
    assert "functions/v1/tenant-provision" not in main, (
        "the dashboard must call the BFF (`/api/provision`), never the Edge "
        "Function directly — a browser caller is refused 403"
    )
    assert "${API_BASE}/provision" in main, (
        "the dashboard's provisioning call must target the same-origin BFF"
    )
