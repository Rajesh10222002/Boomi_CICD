"""
Resolve the packageId to roll back to, for a process+environment: the
package that was actually live in that environment just before the
current one (skip=1), read from deployments/ledger.csv — the deploy
history this repo maintains about itself (see deployments/README.md).

Deliberately has no fallback to global package-creation order via the
Boomi API: a rollback can only ever go to something this environment
actually had deployed before, per the ledger, never an untethered guess.
If there isn't enough history yet, this fails — pass an explicit
package_id instead (one that's genuinely been live in this environment;
check the job summary from a previous promotion, or deployments/ledger.csv
directly).

Always writes a job-summary table of recent deploys to this environment
(with real dates and versions) so the choice can be sanity-checked.

Usage:
    python scripts/resolve_rollback_package.py --name "My Process" --environment qa [--skip 1]
"""

import argparse
import csv
import os

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


def _write_summary(name, environment, skip, rows):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a") as f:
        f.write(f"## Rollback candidates for '{name}' in `{environment}` (from this repo's deployment ledger)\n\n")
        f.write("| # | packageId | version | when | notes |\n|---|---|---|---|---|\n")
        for i, r in enumerate(rows):
            marker = " <- selected" if i == skip else (" (current)" if i == 0 else "")
            f.write(
                f"| {i} | `{r['package_id']}` | {r.get('version', '')} | {r['timestamp']} | "
                f"{r.get('comment', '')}{marker} |\n"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--skip", type=int, default=1, help="0=current, 1=one version back (default)")
    args = parser.parse_args()

    rows = _ledger_rows(args.name, args.environment)
    if len(rows) <= args.skip:
        raise SystemExit(
            f"Only {len(rows)} distinct successful deploy(s) of '{args.name}' to '{args.environment}' "
            f"in the ledger — nothing {args.skip} version(s) back to roll back to. Pass an explicit "
            f"package_id instead, or check deployments/ledger.csv."
        )

    target = rows[args.skip]
    print(f"package-id={target['package_id']}")
    print(f"component-id={target.get('component_id', '')}")
    print(f"version={target.get('version', '')}")
    _write_summary(args.name, args.environment, args.skip, rows)


if __name__ == "__main__":
    main()
