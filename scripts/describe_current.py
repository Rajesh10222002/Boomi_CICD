"""
Print a short, human-readable description of whatever's currently
recorded as deployed to a process+environment, from
deployments/current.csv — for showing a before/after diff to whoever's
approving a deploy, not for any control-flow decision (see
resolve_current_package.py for that).

Prints `current=<description>` (nothing else) so a workflow step can
capture it into $GITHUB_OUTPUT.

Usage:
    python scripts/describe_current.py --name "My Process" --environment qa
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
                    version = row.get("version") or "(no version label)"
                    print(
                        f"current={version} — package {row['package_id']}, "
                        f"deployed {row['deployed_at']} by {row['deployed_by']}"
                    )
                    return

    print("current=(nothing deployed yet)")


if __name__ == "__main__":
    main()
