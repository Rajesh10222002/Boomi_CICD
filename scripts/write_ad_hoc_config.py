"""
Write a one-process components.json-shaped config file for an ad-hoc CI run
triggered from the Streamlit app (workflow_dispatch with an explicit
component_id resolved live from Boomi), bypassing components.json so nobody
has to hand-maintain a static component_id list.

Usage:
    python scripts/write_ad_hoc_config.py --name "My Process" --component-id abc-123 --out ad_hoc_components.json
"""

import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--component-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    config = {
        "processes": [
            {
                "name": args.name,
                "component_id": args.component_id,
                "description": "Ad-hoc run from the Streamlit app",
            }
        ]
    }
    with open(args.out, "w") as f:
        json.dump(config, f, indent=2)


if __name__ == "__main__":
    main()
