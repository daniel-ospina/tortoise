#!/usr/bin/env python3
"""B7 — cohort roll-up for the activation scorecard.

WHY A SEPARATE, COMMITTED SCRIPT
--------------------------------
The beta cohort is structurally MULTI-ORG: the free tier allows one org per
person with no invites, so each tester is their own org, and the programme's
own criterion is cohort-level ("N of 10 ..."). An org-scoped endpoint alone
would report n=1 — a dogfood metric, not an activation metric.

This script therefore drives the SHIPPED product endpoint once per org and sums
the results. It is deliberately the operator's tool, not a product surface:

* The org list is EXPLICIT and operator-supplied — never enumerated from the
  registry. Founder/dogfood orgs are excluded by construction (name your
  cohort), rather than by a heuristic that could silently redefine the number.
* It is NOT reachable from any request handler. A cross-tenant admin endpoint
  IS possible but needs its own tenant-isolation security review; that is filed
  separately, not smuggled in here.
* The output always carries ``cohort_definition``, so the number can never be
  read without its denominator.

IT REFUSES TO REPORT AN ACTIVATION RATE
---------------------------------------
``recall_attempted`` is an ATTEMPT, not an answer — ``mcp_tool_call.status ==
"ok"`` means only that the tool did not raise. ``value_confirmed`` is not
measurable at all. So this tool emits no ``activated`` field and no activation
rate, and it fails loudly if a payload ever grows one: deriving a rate here is
exactly the mislabelling the scorecard exists to remove.

USAGE
-----
    python3 tools/activation_cohort.py \\
        --api-base https://api.premiselabs.co \\
        --org <org_id_a>=<tt_key_a> --org <org_id_b>=<tt_key_b> \\
        --since 2026-09-16T00:00:00Z --until 2026-09-17T00:00:00Z \\
        --out /tmp/b7-cohort.json

Read-only. It performs one GET per org and writes only the ``--out`` file.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

SCORECARD_PATH = "/v1/activation/scorecard"
STAGE_NAMES = ("captured", "stored", "memory_produced", "recall_attempted",
               "value_confirmed")


def _assert_no_activation_claim(payload: dict) -> None:
    """Refuse to consume a payload that fabricates an activation claim."""
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if "activat" in str(key).lower():
                    raise SystemExit(
                        f"scorecard payload carries an activation claim "
                        f"({key!r}) — refusing to roll it up into a cohort "
                        f"number; recall_attempted is an attempt, not value")
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def fetch_scorecard(api_base: str, key: str, since: str | None,
                    until: str | None, timeout: float = 30.0) -> dict:
    """One GET against the org-scoped scorecard. Raises on transport/HTTP error
    — an org we could not read must never be quietly summed as 0."""
    query = {}
    if since:
        query["since"] = since
    if until:
        query["until"] = until
    url = api_base.rstrip("/") + SCORECARD_PATH
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def roll_up(orgs: list[str], payloads: list[dict | None],
            errors: list[str | None] | None = None) -> dict:
    """Sum the per-org stages into one cohort number.

    ONLY ``state == "measured"`` stages are summed. The two non-measured states
    are NOT collapsed into one another:

    * ``unavailable`` — the org failed to report (unreadable, or a malformed
      cell). Named in ``orgs_unavailable``.
    * ``not_measurable`` — the org answered correctly and there is nothing to
      measure (e.g. ``value_confirmed`` always; ``recall_attempted`` for an org
      that has never produced memory). Named in ``orgs_not_measurable``.

    Collapsing the second into the first would assert that a healthy org failed
    to report — the exact state inversion this lane exists to prevent. So the
    cohort stage is ``not_measurable`` only when every org said so, ``partial``
    when some orgs measured and others did not report or had nothing to
    measure, and ``unavailable`` when nothing was measurable for a reason that
    is not "there is nothing to measure".
    """
    errors = errors or [None] * len(orgs)
    stages: dict[str, dict] = {}
    for name in STAGE_NAMES:
        values: list[int] = []
        orgs_unavailable: list[str] = []
        orgs_not_measurable: list[str] = []
        for org_id, payload in zip(orgs, payloads, strict=True):
            if payload is None:
                orgs_unavailable.append(org_id)
                continue
            cell = (payload.get("stages") or {}).get(name)
            if not cell:
                orgs_unavailable.append(org_id)
                continue
            state = cell.get("state")
            if state == "measured" and cell.get("value") is not None:
                values.append(int(cell["value"]))
            elif state == "not_measurable":
                orgs_not_measurable.append(org_id)
            else:
                # `unavailable`, or a `measured` cell with no value — a
                # malformed upstream payload. Counting it as 0 would invent a
                # measurement the org never reported.
                orgs_unavailable.append(org_id)

        measured = len(values)
        if measured and (orgs_unavailable or orgs_not_measurable):
            stages[name] = {"state": "partial", "value": sum(values),
                            "reason": "some_orgs_unavailable_or_not_measurable",
                            "orgs_measured": measured,
                            "orgs_unavailable": orgs_unavailable,
                            "orgs_not_measurable": orgs_not_measurable}
        elif measured:
            stages[name] = {"state": "measured", "value": sum(values),
                            "reason": None, "orgs_measured": measured,
                            "orgs_unavailable": [], "orgs_not_measurable": []}
        elif orgs_unavailable:
            stages[name] = {"state": "unavailable", "value": None,
                            "reason": "no_org_measurable",
                            "orgs_measured": 0,
                            "orgs_unavailable": orgs_unavailable,
                            "orgs_not_measurable": orgs_not_measurable}
        elif orgs_not_measurable:
            # Every org answered, and every answer was "nothing to measure".
            stages[name] = {"state": "not_measurable", "value": None,
                            "reason": "no_org_has_a_measurable_value",
                            "orgs_measured": 0,
                            "orgs_unavailable": [],
                            "orgs_not_measurable": orgs_not_measurable}
        else:
            # Unreachable: --org is required and non-empty. Refuse rather than
            # report a fabricated measured zero.
            stages[name] = {"state": "unavailable", "value": None,
                            "reason": "no_orgs", "orgs_measured": 0,
                            "orgs_unavailable": [], "orgs_not_measurable": []}
    return {
        "cohort_definition": {
            "orgs": list(orgs),
            "size": len(orgs),
            "source": "operator_supplied",
        },
        # A one-org cohort is a dogfood measurement, not stranger activation —
        # the product's own gate vocabulary (product-success-eval.md: internal
        # gate vs public gate).
        "dogfood_only": len(orgs) == 1,
        "stages": stages,
        "org_errors": {o: e for o, e in zip(orgs, errors, strict=True) if e},
        "note": ("recall_attempted is an ATTEMPT, not an answer; value_confirmed "
                 "is a refusal. No activation rate is derivable from this output."),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api-base", required=True,
                    help="e.g. https://api.premiselabs.co")
    ap.add_argument("--org", action="append", required=True, metavar="ORG_ID=KEY",
                    help="explicit cohort member (repeatable); never derived")
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    orgs: list[str] = []
    keys: list[str] = []
    for spec in args.org:
        if "=" not in spec:
            ap.error(f"--org must be ORG_ID=KEY, got {spec!r}")
        org_id, key = spec.split("=", 1)
        orgs.append(org_id)
        keys.append(key)

    payloads: list[dict | None] = []
    errors: list[str | None] = []
    for org_id, key in zip(orgs, keys, strict=True):
        try:
            payload = fetch_scorecard(args.api_base, key, args.since, args.until)
            _assert_no_activation_claim(payload)
            payloads.append(payload)
            errors.append(None)
        except SystemExit:
            raise
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP {exc.code}")
            payloads.append(None)
            print(f"  ! {org_id}: HTTP {exc.code}", file=sys.stderr)
        except Exception as exc:
            errors.append(type(exc).__name__)
            payloads.append(None)
            print(f"  ! {org_id}: {type(exc).__name__}: {exc}", file=sys.stderr)

    report = roll_up(orgs, payloads, errors)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
