"""
Resolve the packageId to promote for a process, from an environment one
level down (Dev for a qa promotion, QA for a prod promotion).

Without --version: the packageId currently recorded as deployed there,
from deployments/current.csv — this repo's own record of what's actually
live where (see deployments/README.md).

With --version: an *older* packageId that was successfully deployed there
at some point (not necessarily the current one), looked up by its version
label from deployments/ledger.csv. Either way, only ever resolves to
something the ledger recorded as actually deployed to that environment —
never an arbitrary "latest built" guess.

Fails if there's no matching row: a process (or a specific version of it)
can only be promoted from an environment it's actually been deployed to,
Dev -> QA -> PD. That's the point, not a bug.

Prints `package-id=<id>`, `component-id=<id>`, and `version=<value>`
(nothing else) so a workflow step can capture all three into
$GITHUB_OUTPUT. Diagnostics go to stderr.

Usage:
    python scripts/resolve_current_package.py --name "My Process" --environment dev
    python scripts/resolve_current_package.py --name "My Process" --environment dev --version v1.0
"""

import argparse
import csv
import os

_HERE = os.path.dirname(__file__)
CURRENT_PATH = os.path.join(_HERE, "..", "deployments", "current.csv")
LEDGER_PATH = os.path.join(_HERE, "..", "deployments", "ledger.csv")


def _resolve_current(name, environment):
    if os.path.exists(CURRENT_PATH):
        with open(CURRENT_PATH, newline="") as f:
            for row in csv.DictReader(f):
                if row["process_name"] == name and row["environment"] == environment:
                    return row["package_id"], row["component_id"], row.get("version", "")
    return None


def _resolve_by_version(name, environment, version):
    if not os.path.exists(LEDGER_PATH):
        return None
    match = None
    with open(LEDGER_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if (
                row["process_name"] == name
                and row["environment"] == environment
                and row.get("version", "") == version
                and row["status"] == "success"
            ):
                match = row  # ledger.csv is append-only; keep the last (most recent) match
    if match is None:
        return None
    return match["package_id"], match["component_id"], match.get("version", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--version", default="", help="Promote this specific version instead of whatever's current")
    args = parser.parse_args()

    if args.version:
        result = _resolve_by_version(args.name, args.environment, args.version)
        if result is None:
            raise SystemExit(
                f"'{args.name}' version '{args.version}' has never been successfully deployed to "
                f"'{args.environment}' — check deployments/ledger.csv for what's actually available."
            )
    else:
        result = _resolve_current(args.name, args.environment)
        if result is None:
            raise SystemExit(
                f"'{args.name}' has never been successfully deployed to '{args.environment}' "
                f"(no row in deployments/current.csv) — promote it there first before promoting further."
            )

    package_id, component_id, version = result
    print(f"package-id={package_id}")
    print(f"component-id={component_id}")
    print(f"version={version}")


if __name__ == "__main__":
    main()
