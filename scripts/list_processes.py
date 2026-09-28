"""
One-off helper: list live components (processes, by default) in the Boomi
account, plus each process's latest package id, so the values needed for
the ci.yml / cd.yml workflow_dispatch forms (componentId, packageId) can be
looked up instead of hand-maintained anywhere.

Usage:
    python scripts/list_processes.py [--type process]

Needs BOOMI_ACCOUNT_ID / BOOMI_USERNAME / BOOMI_API_TOKEN set (see
boomi_client.py) — export them locally to run this by hand, or use the
"List Boomi Processes" GitHub Actions workflow (workflow_dispatch) to run it
against the repo secrets without ever putting the token in a terminal here.
"""

import argparse
import os

from boomi_client import BoomiClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", default="process", help="Boomi component type to list (default: process)")
    args = parser.parse_args()

    client = BoomiClient()
    components = client.query_components(component_type=args.type)
    show_packages = args.type == "process"

    if not components:
        print(f"No components of type '{args.type}' found in this account.")
        return

    if show_packages:
        for c in components:
            c["latestPackageId"] = client.find_latest_package(c["componentId"])

    print(f"{len(components)} component(s) of type '{args.type}':\n")
    header = f"{'name':<40} {'componentId':<40}" + (" latestPackageId" if show_packages else "")
    print(header)
    for c in components:
        row = f"{c.get('name', ''):<40} {c.get('componentId', ''):<40}"
        if show_packages:
            row += f" {c.get('latestPackageId') or 'none yet'}"
        print(row)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## Boomi components (type={args.type})\n\n")
            if show_packages:
                f.write("| name | componentId | latestPackageId |\n|---|---|---|\n")
                for c in components:
                    f.write(f"| {c.get('name', '')} | `{c.get('componentId', '')}` | "
                            f"`{c.get('latestPackageId') or 'none yet'}` |\n")
            else:
                f.write("| name | componentId |\n|---|---|\n")
                for c in components:
                    f.write(f"| {c.get('name', '')} | `{c.get('componentId', '')}` |\n")


if __name__ == "__main__":
    main()
