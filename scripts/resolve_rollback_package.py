"""
Resolve the packageId to roll back to, for a process+environment: by
default, the package that was actually live in that environment just
before the current one (skip=1), read from deployments/ledger.csv — the
deploy history this repo maintains about itself (see
deployments/README.md), not the Boomi API.

Falls back to global package-creation order for the component (ignoring
environment, via the Boomi API) if the ledger doesn't have enough
successful rows for this process+environment yet — e.g. right after the
ledger was introduced, before enough promotions have gone through it.

Always writes a job-summary table of recent deploys to this environment
(with real dates and whichever source — ledger or fallback — was used) so
the choice can be sanity-checked or overridden with an explicit
package_id.

Usage:
    python scripts/resolve_rollback_package.py --name "My Process" --environment qa [--skip 1]
"""

import argparse
import csv
import os

from boomi_client import BoomiClient

LEDGER_PATH = os.path.join(os.path.dirname(__file__), "..", "deployments", "ledger.csv")


def _ledger_rows(name, environment):
    if not os.path.exists(LEDGER_PATH):
        return []
    with open(LEDGER_PATH, newline="") as f:
        rows = [
            r
            for r in csv.DictReader(f)
            if r["process_name"] == name and r["environment"] == environment and r["status"] == "success"
        ]
    # ledger.csv is append-only, so file order is already chronological —
    # reverse it for newest-first instead of re-sorting by the timestamp
    # *string*, which breaks ties wrong (stable sort keeps the earlier one
    # first even with reverse=True) when two rows land in the same second.
    rows.reverse()
    # Collapse consecutive re-deploys of the same package_id so "skip 1"
    # means "the previous *distinct* version", not "the same version again".
    deduped = []
    for r in rows:
        if not deduped or deduped[-1]["package_id"] != r["package_id"]:
            deduped.append(r)
    return deduped


def _write_summary(component_id, environment, skip, source, entries):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a") as f:
        f.write(f"## Rollback candidates for componentId `{component_id}` in `{environment}` ({source})\n\n")
        f.write("| # | packageId | when | notes |\n|---|---|---|---|\n")
        for i, (package_id, when, notes) in enumerate(entries):
            marker = " <- selected" if i == skip else (" (current)" if i == 0 else "")
            f.write(f"| {i} | `{package_id}` | {when} | {notes}{marker} |\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--skip", type=int, default=1, help="0=current, 1=one version back (default)")
    args = parser.parse_args()

    client = BoomiClient()
    component_id = client.find_component_by_name(args.name)

    ledger_rows = _ledger_rows(args.name, args.environment)
    if len(ledger_rows) > args.skip:
        target = ledger_rows[args.skip]
        print(f"package-id={target['package_id']}")
        _write_summary(
            component_id,
            args.environment,
            args.skip,
            "this repo's deployment ledger",
            [(r["package_id"], r["timestamp"], r.get("comment", "")) for r in ledger_rows],
        )
        return

    # Fallback: not enough per-environment history yet.
    packages = client.list_packages(component_id, limit=args.skip + 1)
    if len(packages) <= args.skip:
        raise SystemExit(
            f"Only {len(packages)} package(s) exist for '{args.name}' and no ledger history for "
            f"'{args.environment}' — nothing {args.skip} version(s) back to roll back to."
        )
    target = packages[args.skip]
    print(f"package-id={target['packageId']}")
    _write_summary(
        component_id,
        args.environment,
        args.skip,
        "no ledger history yet — fell back to global package order, NOT per-environment",
        [(p.get("packageId", ""), p.get("createdDate", ""), p.get("notes", "")) for p in packages],
    )


if __name__ == "__main__":
    main()
