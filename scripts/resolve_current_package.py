"""
Resolve the packageId currently recorded as deployed to a given
environment for a process, from deployments/current.csv — this repo's own
record of what's actually live where (see deployments/README.md).

Deliberately has no fallback to "the latest packaged version overall": a
process can only be promoted from an environment it has actually been
deployed to (Dev -> QA -> PD), never from an arbitrary package that was
built but never deployed there. If there's no ledger row, this fails —
that's the point, not a bug: run "Build & Deploy to Dev" (or promote to
the environment below) first.

Prints `package-id=<id>`, `component-id=<id>`, and `version=<value>`
(nothing else) so a workflow step can capture all three into
$GITHUB_OUTPUT. Diagnostics go to stderr.

Usage:
    python scripts/resolve_current_package.py --name "My Process" --environment dev
"""

import argparse
import csv
import os

CURRENT_PATH = os.path.join(os.path.dirname(__file__), "..", "deployments", "current.csv")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    args = parser.parse_args()

    if os.path.exists(CURRENT_PATH):
        with open(CURRENT_PATH, newline="") as f:
            for row in csv.DictReader(f):
                if row["process_name"] == args.name and row["environment"] == args.environment:
                    print(f"package-id={row['package_id']}")
                    print(f"component-id={row['component_id']}")
                    print(f"version={row.get('version', '')}")
                    return

    raise SystemExit(
        f"'{args.name}' has never been successfully deployed to '{args.environment}' "
        f"(no row in deployments/current.csv) — promote it there first before promoting further."
    )


if __name__ == "__main__":
    main()
