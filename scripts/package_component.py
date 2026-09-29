"""
Package every process listed in components/components.json.
Writes the resulting package IDs to packages.json so later steps
(deploy, tests) know what version they're working with.

Usage:
    python scripts/package_component.py --config components/components.json --version v1.1 --out packages.json
"""

import argparse
import json

from boomi_client import BoomiClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="components.json path")
    parser.add_argument("--version", required=True, help="Version label for this package, e.g. v1.1")
    parser.add_argument("--out", default="packages.json", help="where to write resulting package IDs")
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    client = BoomiClient()
    results = []
    for proc in config["processes"]:
        component_id = proc["component_id"]
        if component_id.startswith("REPLACE-WITH"):
            raise SystemExit(
                f"components.json still has a placeholder component_id for '{proc['name']}'. "
                "Fill in the real Boomi component GUID before running CI."
            )
        package_id = client.create_packaged_component(
            component_id, package_version=args.version, notes=f"CI package for {proc['name']} ({args.version})"
        )
        print(f"Packaged {proc['name']} ({component_id}) -> {package_id} [{args.version}]")
        results.append(
            {"name": proc["name"], "component_id": component_id, "package_id": package_id, "version": args.version}
        )

    with open(args.out, "w") as f:
        json.dump({"packages": results}, f, indent=2)


if __name__ == "__main__":
    main()
