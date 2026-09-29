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
