"""
Look up a package_id's component_id/version from this repo's own deploy
history (deployments/ledger.csv), for a caller that already knows the
package_id but not its metadata — specifically cd.yml's explicit-
package_id branch, used only by rollback.yml (a normal promotion always
resolves component_id/version together via resolve_current_package.py,
so it never needs this). Same lookup update_deployment_ledger.py does
internally to self-heal a ledger row that's missing this info; this
exposes it to a workflow step earlier, for display in the tracking issue
and job summary before that self-heal ever runs.

Prints `component-id=<id or empty>` and `version=<value or empty>` —
empty for both if the package_id has no ledger row at all (e.g. it
predates the ledger's version tracking).

Usage:
    python scripts/lookup_ledger_metadata.py --package-id abc-123
"""

import argparse
import csv
import os

LEDGER_PATH = os.path.join(os.path.dirname(__file__), "..", "deployments", "ledger.csv")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-id", required=True)
    args = parser.parse_args()

    component_id, version = "", ""
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH, newline="") as f:
            for row in csv.DictReader(f):
                if row["package_id"] == args.package_id:
                    component_id = row.get("component_id", "")
                    version = row.get("version", "")
                    # keep scanning: ledger.csv is append-only, so the last match is the most recent

    print(f"component-id={component_id}")
    print(f"version={version}")


if __name__ == "__main__":
    main()
