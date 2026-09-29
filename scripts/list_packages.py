"""
List every package (not just the latest) for one Boomi process, newest
first, so an older packageId can be picked as a cd.yml rollback target.
Each package is annotated with which environment(s) this repo's own
deployment ledger (deployments/ledger.csv) recorded it as successfully
deployed to, if any, and whether it's still the *current* one there (from
deployments/current.csv) — see deployments/README.md. A package Boomi
knows about but this repo never deployed shows no environments.

Usage:
    python scripts/list_packages.py --name "My Process"
    python scripts/list_packages.py --component-id abc-123
"""

import argparse
import csv
import os

from boomi_client import BoomiClient

_HERE = os.path.dirname(__file__)
LEDGER_PATH = os.path.join(_HERE, "..", "deployments", "ledger.csv")
CURRENT_PATH = os.path.join(_HERE, "..", "deployments", "current.csv")


def _environments_for_package(package_id):
    """Every environment this package_id was ever successfully deployed to, in first-seen order."""
    envs = []
    if not os.path.exists(LEDGER_PATH):
        return envs
    with open(LEDGER_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if row["package_id"] == package_id and row["status"] == "success" and row["environment"] not in envs:
                envs.append(row["environment"])
    return envs


def _current_environments_for_package(package_id):
    """Environments where this package_id is still the current one right now."""
    result = set()
    if not os.path.exists(CURRENT_PATH):
        return result
    with open(CURRENT_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if row["package_id"] == package_id:
                result.add(row["environment"])
    return result


def _environment_summary(package_id):
    envs = _environments_for_package(package_id)
    if not envs:
        return "—"
    current = _current_environments_for_package(package_id)
    return ", ".join(f"{e} (current)" if e in current else e for e in envs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="", help="Process name to resolve to a componentId")
    parser.add_argument("--component-id", default="", help="Boomi componentId directly")
    args = parser.parse_args()

    # Not a mutually-exclusive-group(required=True): the calling workflow
    # always passes both flags (one may be ""), so argparse would only
    # check that the flags were *passed*, not that either has a value —
    # leaving both form fields blank would otherwise reach the Boomi API
    # with an empty name and fail with a confusing LookupError instead of
    # this clear message.
    if not args.name and not args.component_id:
        raise SystemExit("Provide either --name or --component-id — both were left blank.")

    client = BoomiClient()
    component_id = args.component_id or client.find_component_by_name(args.name)
    packages = client.list_packages(component_id)

    if not packages:
        print(f"No packages found for componentId {component_id}.")
        return

    print(f"{len(packages)} package(s) for componentId {component_id} (newest first):\n")
    print(f"{'packageId':<40} {'created':<25} {'environment(s)':<25} notes")
    for p in packages:
        env_summary = _environment_summary(p.get("packageId", ""))
        print(f"{p.get('packageId', ''):<40} {p.get('createdDate', ''):<25} {env_summary:<25} {p.get('notes', '')}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## Packages for componentId `{component_id}`\n\n")
            f.write("| packageId | created | environment(s) | notes |\n|---|---|---|---|\n")
            for p in packages:
                env_summary = _environment_summary(p.get("packageId", ""))
                f.write(f"| `{p.get('packageId', '')}` | {p.get('createdDate', '')} | {env_summary} | {p.get('notes', '')} |\n")


if __name__ == "__main__":
    main()
