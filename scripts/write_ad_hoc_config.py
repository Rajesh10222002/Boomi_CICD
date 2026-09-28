"""
Write a one-process components.json-shaped config file for an ad-hoc CI run
triggered manually (workflow_dispatch), bypassing components.json so nobody
has to hand-maintain a static component_id list. If --component-id is left
blank, resolves it live from Boomi by --name instead.

Prints `resolved-component-id=<id>` on its own line to stdout (nothing
else) so a workflow step can capture it directly into $GITHUB_OUTPUT;
anything else printed goes to stderr.

Usage:
    python scripts/write_ad_hoc_config.py --name "My Process" --out ad_hoc_components.json
    python scripts/write_ad_hoc_config.py --name "My Process" --component-id abc-123 --out ad_hoc_components.json
"""

import argparse
import json
import sys

from boomi_client import BoomiClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--component-id", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    component_id = args.component_id
    if not component_id:
        client = BoomiClient()
        component_id = client.find_component_by_name(args.name)
        print(f"Resolved '{args.name}' -> componentId {component_id}", file=sys.stderr)

    config = {
        "processes": [
            {
                "name": args.name,
                "component_id": component_id,
                "description": "Ad-hoc run from workflow_dispatch",
            }
        ]
    }
    with open(args.out, "w") as f:
        json.dump(config, f, indent=2)

    print(f"resolved-component-id={component_id}")


if __name__ == "__main__":
    main()
