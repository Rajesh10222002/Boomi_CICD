"""
Resolve the packageId currently recorded as deployed to a given
environment for a process, from deployments/current.csv — this repo's own
record of what's actually live where (see deployments/README.md), not
"the most recently built package overall". Used by cd.yml so promoting to
qa/prod means "take what's currently in the environment below", matching
a real Dev -> QA -> PD chain.

Falls back to the Boomi-side "latest ever packaged" lookup if there's no
ledger row yet (e.g. before the first ledger-writing run for this
process), so promotion still works before any history exists.

Prints `package-id=<id>` and `component-id=<id>` (nothing else) so a
workflow step can capture both into $GITHUB_OUTPUT. Diagnostics go to
stderr.

Usage:
    python scripts/resolve_current_package.py --name "My Process" --environment dev
"""

import argparse
import csv
import os
import sys

from boomi_client import BoomiClient

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
                    return

    print(
        f"No ledger record of '{args.name}' deployed to '{args.environment}' yet — "
        "falling back to the latest packaged version overall.",
        file=sys.stderr,
    )
    client = BoomiClient()
    component_id = client.find_component_by_name(args.name)
    package_id = client.find_latest_package(component_id)
    if not package_id:
        raise SystemExit(f"No package has ever been built for '{args.name}' — run ci.yml for it first.")
    print(f"package-id={package_id}")
    print(f"component-id={component_id}")


if __name__ == "__main__":
    main()
