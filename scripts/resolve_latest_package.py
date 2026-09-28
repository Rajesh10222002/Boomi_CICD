"""
Resolve a process name to its latest packageId. Used by cd.yml when
package_id is left blank on the workflow_dispatch form, so promoting only
ever requires typing a process name.

Prints `package-id=<id>` on its own line to stdout (nothing else) so a
workflow step can capture it directly into $GITHUB_OUTPUT.

Usage:
    python scripts/resolve_latest_package.py --name "My Process"
"""

import argparse

from boomi_client import BoomiClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    client = BoomiClient()
    component_id = client.find_component_by_name(args.name)
    package_id = client.find_latest_package(component_id)
    if not package_id:
        raise SystemExit(f"No package has ever been built for '{args.name}' — run ci.yml for it first.")

    print(f"package-id={package_id}")


if __name__ == "__main__":
    main()
