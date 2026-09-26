#!/usr/bin/env python3
"""#3912 — repair FALSE `decide-completed` onboarding completions.

#3784 made the onboarding writer fail-closed (a `decide-completed` edge is
filed only on an observed decision-shaped write) but that fix is FORWARD-ONLY.
An org that received the edge from the old
``mcp_server._maybe_onboarding_auto_complete`` — which filed one on ANY
successful point write and wrote ``status = complete`` directly — keeps both
facts forever: ``COMPLETED_STEP`` edges are first-write-wins and ``status`` is
monotonic. This script is the remediation path for that already-written state.

FALSENESS GATE (fail-closed)
    A `decide-completed` edge is repaired ONLY when the graph holds the edge
    and holds NO decision evidence at all — no decision-shaped Point and no
    decision Event (see ``state.DECISION_EVIDENCE_POINT_KINDS``, deliberately
    generous over BOTH documented decide protocols, #3916). A TRUE completion
    is reported and left untouched; an unreadable graph is reported
    ``unconfirmable`` and left untouched. The script never repairs on request.

The repair removes the unearned edge AND regresses the status the edge earned
(either alone leaves the served verdict false), and stamps both on the
OnboardingState node, so the repair is INSPECTABLE afterwards.

GUARD
    ``--check`` is the durable guard: a read-only sweep that exits non-zero
    when any false edge exists anywhere in the store. Re-run it after a repair
    to confirm — and after a deploy, to catch a re-falsification.

    ⚠️ The deployed service is `main` WITHOUT #3949, whose writer still files
    `decide-completed` on any point write — so a store-wide `--apply` run
    before #3949 deploys can be RE-FALSIFIED by the org's next write. Re-run
    `--check` after any such write; #3949 closes the window.

    ⚠️ Run `--apply` when the store is quiet. The falseness test and the
    status regression are separate round trips (the per-org lock is
    in-process only, and the deployed service is another writer entirely), so
    a decision filed between them is not re-observed — the repair would then
    remove an edge the org had just legitimately earned.

Usage:
    python3 graph-scripts/repair_false_onboarding_completion.py --check
        # read-only guard; exit 1 when any false edge is present
    python3 graph-scripts/repair_false_onboarding_completion.py
        # dry-run report of every decide-completed holder (no writes)
    python3 graph-scripts/repair_false_onboarding_completion.py --apply
        # apply the repairs (local URIs only unless --allow-remote)

Env: TORTOISE_DB_URI (or --uri) — the store to sweep.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from tortoise.onboarding import state as _os  # noqa: E402


def _is_remote(uri: str) -> bool:
    """True for anything that is not an in-process/loopback target."""
    if not uri:
        return False
    if uri.startswith("redislite") or uri.startswith("embedded"):
        return False
    try:
        parsed = urlparse(uri if "://" in uri else f"redis://{uri}")
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    return host not in ("", "localhost", "127.0.0.1", "::1", "host.docker.internal")


def _list_graphs() -> list[str]:
    """Server-wide graph names — a read that never mints a graph."""
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(namespace="registry")
    try:
        return sorted(sdk._get_proj().db.list_graphs() or [])
    finally:
        sdk.close()


def _proj_for(graph_name: str):
    """Projection on an ALREADY-LISTED graph name (never mints one)."""
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(graph_name=graph_name)
    return sdk, sdk._get_proj()


def _read_target(graph_name: str, listed: set[str]) -> str | None:
    """The name the deployed read path opens — `org_<id>` if listed, else the
    legacy `team_<id>` (`hosted_api._open_org_graph_sdk`). None when neither
    is listed or the name is NOT a tenant graph.

    A scoped name (`org_<tid>_<gid>`) yields `None`: the deployed reader
    derives `org_{org_id}` from the namespace rule, so a scoped name is never
    a read target — and treating it as one would put it in `known` and excuse
    the scoped graph from the `_has_scoped_graphs` skip below."""
    if "_" not in graph_name:
        return None
    gid = graph_name.split("_", 1)[1]
    if "_" in gid:  # org ids never contain `_` — this is a graph-scoped name
        return None
    for candidate in (f"org_{gid}", f"team_{gid}"):
        if candidate in listed:
            return candidate
    return None


def _org_ids(graph_name: str, proj) -> list[str]:
    """org_id(s) of the OnboardingState node(s) in this graph, in graph order."""
    rows = proj.query(
        f"MATCH (n:{_os.ONBOARDING_NODE_LABEL}) RETURN n.org_id").result_set
    ids = [r[0] for r in rows if r and r[0]]
    if ids:
        return ids
    # Namespace fallback: an empty-id node can still be keyed by graph name.
    return [graph_name.split("_", 1)[1]] if "_" in graph_name else []


def _has_scoped_graphs(org_id: str, listed: set[str],
                       read_target: str | None = None) -> bool:
    """True when the org has a graph OTHER than its tenant graph(s).

    A decision filed through a graph-scoped key lands in a non-default graph
    (`org_{tid}_{gid}`, the pre-#3543 `team_{tid}_{gid}`, or a namespace
    passed to `sdk.create_org_graph`), not in the read target — so the read
    target's evidence scan would call the org FALSE and delete a completion
    it really earned. Those orgs are SKIPPED, reported, never silently
    repaired.

    Detection is by the org id as a SUBSTRING of the raw graph list (covers
    both tenant prefixes and the usual scoped shape), cross-checked against
    the registry's own `graph_list(org_id)` when that is readable. The
    registry rows carry the DB graph name in **`namespace`**, NOT in `name`
    (the default graph's `name` is the display string ``"default"``) —
    keying on `name` made every normally-provisioned org look scoped. NOTE: a
    caller-supplied namespace that does not embed the org id AND is not in the
    registry is NOT detectable here — a documented residual, not a claim of
    coverage. An unreadable registry is treated as SCOPED (skip), never as
    clear.
    """
    known = {f"org_{org_id}", f"team_{org_id}", read_target}
    if any(org_id in name and name not in known for name in listed):
        return True
    handle = None
    try:
        from tortoise.sdk import TortoiseSDK
        handle = TortoiseSDK(namespace="registry")
        names = {r.get("namespace") for r in handle.graph_list(org_id)}
    except Exception:  # noqa: BLE001, RUF100
        return True  # cannot confirm → fail-closed, never repair
    finally:
        if handle is not None:
            import contextlib
            with contextlib.suppress(Exception):
                handle.close()
    return any(n and n not in known for n in names)


def sweep(org_filter: str | None = None) -> list[dict]:
    """Every `decide-completed` holder in the store with its verdict."""
    listed = set(_list_graphs())
    out: list[dict] = []
    for graph_name in sorted(listed):
        sdk = proj = None
        try:
            sdk, proj = _proj_for(graph_name)
            for org_id in _org_ids(graph_name, proj):
                if org_filter and org_id != org_filter:
                    continue
                finding = _os.find_false_decide_completion(proj, org_id)
                if finding["verdict"] not in ("true", "false", "unconfirmable"):
                    continue
                target = _read_target(graph_name, listed)
                # The scoped override applies only where it changes the VERDICT:
                # a decision-backed (`true`) edge stays true, so a store with
                # custom graphs can still reach a green guard.
                scoped = (finding["verdict"] in ("false", "unconfirmable")
                          and _has_scoped_graphs(org_id, listed, target))
                out.append({
                    "graph": graph_name,
                    "org_id": org_id,
                    "read_target": target,
                    "served": target == graph_name,
                    "verdict": "scoped-graphs" if scoped else finding["verdict"],
                    "skipped_reason": (
                        "org has non-default graphs; a decision may live "
                        "outside the read target" if scoped else None),
                    "half_repaired": finding["half_repaired"],
                    "status": finding["status"],
                    "completed_steps": finding["completed_steps"],
                    "decision_points": (finding["evidence"] or {}).get(
                        "decision_points"),
                    "decision_events": (finding["evidence"] or {}).get(
                        "decision_events"),
                })
        except Exception as exc:  # noqa: BLE001, RUF100
            out.append({"graph": graph_name, "org_id": None,
                        "verdict": "unconfirmable", "error": str(exc)})
        finally:
            if sdk is not None:
                sdk.close()
    return out


def _apply(rows: list[dict]) -> list[dict]:
    """Repair every row whose verdict is `false`; leave everything else."""
    results: list[dict] = []
    for row in rows:
        if row.get("verdict") != "false":
            results.append({**row, "action": f"skipped-{row['verdict']}"})
            continue
        sdk = proj = None
        try:
            sdk, proj = _proj_for(row["graph"])
            res = _os.repair_false_decide_completion(
                proj, row["org_id"], apply=True)
            # Re-read so the report shows the POST-repair state, not the
            # pre-repair row it started from.
            after = _os.find_false_decide_completion(proj, row["org_id"])
            results.append({**row, **res,
                            "status_after": after["status"],
                            "completed_steps_after": after["completed_steps"],
                            "verdict_after": after["verdict"]})
        except Exception as exc:  # noqa: BLE001, RUF100
            results.append({**row, "action": "FAILED", "error": str(exc)})
        finally:
            if sdk is not None:
                sdk.close()
    return results


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("No `decide-completed` edge found anywhere in the store.")
        return
    print(f"{'graph':<40} {'verdict':<14} {'status':<9} served  "
          f"dec/evid  steps")
    for r in rows:
        print(f"{r.get('graph')!s:<40} {r.get('verdict')!s:<14} "
              f"{r.get('status_after') or r.get('status')!s:<9} "
              f"{r.get('served')!s:<7} "
              f"{r.get('decision_points')}/{r.get('decision_events')}       "
              f"{','.join(r.get('completed_steps_after') or r.get('completed_steps') or [])}")
        if r.get("action"):
            extra = ""
            if r.get("still_complete_without_the_edge"):
                extra = " (status left alone — the org is complete without the edge)"
            print(f"    action: {r['action']}{extra}")
        if r.get("error"):
            print(f"    error: {r['error']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#3912: repair false decide-completed onboarding edges")
    ap.add_argument("--apply", action="store_true",
                    help="write the repairs (default: dry-run report)")
    ap.add_argument("--check", action="store_true",
                    help="read-only guard: exit 1 when any false edge exists")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", ""),
                    help="store URI (default: $TORTOISE_DB_URI)")
    ap.add_argument("--org", default=None, help="only this org_id")
    ap.add_argument("--allow-remote", action="store_true",
                    help="required to --apply against a non-loopback URI")
    ap.add_argument("--report", default=None, help="write a JSON report here")
    args = ap.parse_args()

    # The guard inspects the EFFECTIVE uri — the value the SDK will actually
    # use. Inspecting `args.uri` alone let `--uri ''` with a remote
    # $TORTOISE_DB_URI look local while the SDK wrote to the remote store.
    effective_uri = args.uri or os.environ.get("TORTOISE_DB_URI", "")
    if args.uri:
        os.environ["TORTOISE_DB_URI"] = args.uri
    remote = _is_remote(effective_uri)
    writing = args.apply
    if writing and remote and not args.allow_remote:
        print("🛑 refusing --apply against a non-loopback store without "
              "--allow-remote (this is the remote-write guard)")
        return 2
    print(f"Mode: {'APPLY (writes)' if writing else 'READ-ONLY'}"
          f"{' [REMOTE]' if remote else ''}  store={effective_uri or 'embedded'}")

    # `--check` is the STORE-WIDE guard by contract ("exit 1 when any false
    # edge exists") — narrowing it with --org would report GREEN while a false
    # edge exists elsewhere. --org is honored for the report/apply paths only.
    rows = sweep(org_filter=None if args.check else args.org)
    if args.check and args.org:
        print("(--check is store-wide by contract — --org ignored for the guard)")
    _print_table(rows)
    false_rows = [r for r in rows if r.get("verdict") == "false"]
    true_rows = [r for r in rows if r.get("verdict") == "true"]
    unconfirmable = [r for r in rows if r.get("verdict") == "unconfirmable"]
    scoped = [r for r in rows if r.get("verdict") == "scoped-graphs"]
    print(f"\nHolders: {len(rows)}  false: {len(false_rows)} "
          f"({sum(1 for r in false_rows if r.get('served'))} served)  "
          f"true: {len(true_rows)}  unconfirmable: {len(unconfirmable)}  "
          f"skipped-scoped-graphs: {len(scoped)}")
    half = [r for r in false_rows if r.get("half_repaired")]
    if half:
        print(f"  of the false rows, {len(half)} are HALF-REPAIRED "
              f"(edge already gone, status regression outstanding)")
    if scoped:
        print("  ⚠️ skipped (NOT repaired, needs a human):")
        for r in scoped:
            print(f"     {r.get('graph')} — {r.get('skipped_reason')}")

    if args.check:
        if false_rows or unconfirmable:
            print("GUARD RED — false or unconfirmable decide-completed edges "
                  "present")
            return 1
        if scoped:
            print("GUARD AMBER — no false edge, but N org(s) could not be "
                  "confirmed decision-free (non-default graphs)")
            return 3
        print("GUARD GREEN — every decide-completed edge is decision-backed")
        return 0

    if writing and false_rows:
        print("\nApplying repairs …")
        results = _apply(rows)
        _print_table(results)
        failed = [r for r in results if r.get("action") == "FAILED"]
        repaired = [r for r in results if r.get("action") == "repaired"]
        print(f"\nRepaired: {len(repaired)}  failed: {len(failed)}")
        rows = results
        if failed:
            print("⚠️  partial repair — see FAILED rows above")
    elif writing:
        print("\nNothing to repair.")

    if args.report:
        with open(args.report, "w") as fh:
            json.dump(rows, fh, indent=1)
        print(f"report → {args.report}")

    if unconfirmable or any(r.get("action") == "FAILED" for r in rows):
        return 1
    if scoped:
        return 3  # unverified, not silently clean — see the report
    return 0


if __name__ == "__main__":
    sys.exit(main())
