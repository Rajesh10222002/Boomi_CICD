"""
One-off helper: list live components (processes, by default) in the Boomi
account, so the real pilot process's component_id can be picked instead of
the placeholder in components/components.json.

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

    if not components:
        print(f"No components of type '{args.type}' found in this account.")
        return

    print(f"{len(components)} component(s) of type '{args.type}':\n")
    print(f"{'name':<50} componentId")
    for c in components:
        print(f"{c.get('name', ''):<50} {c.get('componentId', '')}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## Boomi components (type={args.type})\n\n")
            f.write("| name | componentId |\n|---|---|\n")
            for c in components:
                f.write(f"| {c.get('name', '')} | `{c.get('componentId', '')}` |\n")


if __name__ == "__main__":
    main()
