"""
List every package (not just the latest) for one Boomi process, newest
first, so an older packageId can be picked as a cd.yml rollback target.

Usage:
    python scripts/list_packages.py --name "My Process"
    python scripts/list_packages.py --component-id abc-123
"""

import argparse
import os

from boomi_client import BoomiClient


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", help="Process name to resolve to a componentId")
    group.add_argument("--component-id", help="Boomi componentId directly")
    args = parser.parse_args()

    client = BoomiClient()
    component_id = args.component_id or client.find_component_by_name(args.name)
    packages = client.list_packages(component_id)

    if not packages:
        print(f"No packages found for componentId {component_id}.")
        return

    print(f"{len(packages)} package(s) for componentId {component_id} (newest first):\n")
    print(f"{'packageId':<40} {'created':<25} notes")
    for p in packages:
        print(f"{p.get('packageId', ''):<40} {p.get('createdDate', ''):<25} {p.get('notes', '')}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## Packages for componentId `{component_id}`\n\n")
            f.write("| packageId | created | notes |\n|---|---|---|\n")
            for p in packages:
                f.write(f"| `{p.get('packageId', '')}` | {p.get('createdDate', '')} | {p.get('notes', '')} |\n")


if __name__ == "__main__":
    main()
