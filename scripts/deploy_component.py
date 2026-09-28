"""
Deploy one packaged component to a target environment (dev | qa | prod).

Usage:
    # From a CI-produced packages.json (by process name):
    python scripts/deploy_component.py --process-name PLACEHOLDER-sample-process \
        --packages packages.json --env dev --config environments/dev.json

    # Or with an explicit package id (what the Streamlit app / cd.yml uses):
    python scripts/deploy_component.py --package-id abc123 --env qa \
        --config environments/qa.json
"""

import argparse
import json

from boomi_client import BoomiClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="environments/<env>.json path")
    parser.add_argument("--env", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--package-id", help="Packaged component id to deploy")
    parser.add_argument("--packages", help="packages.json to look up --process-name in")
    parser.add_argument("--process-name", help="Name from components.json, used with --packages")
    args = parser.parse_args()

    package_id = args.package_id
    if not package_id:
        if not (args.packages and args.process_name):
            raise SystemExit("Provide either --package-id, or both --packages and --process-name")
        with open(args.packages) as f:
            packages = json.load(f)["packages"]
        match = next((p for p in packages if p["name"] == args.process_name), None)
        if not match:
            raise SystemExit(f"No package found for process '{args.process_name}' in {args.packages}")
        package_id = match["package_id"]

    with open(args.config) as f:
        env_config = json.load(f)

    environment_id = env_config["environment_id"]
    if environment_id.startswith("REPLACE-WITH"):
        raise SystemExit(f"{args.config} still has a placeholder environment_id. Fill it in first.")

    client = BoomiClient()
    deployment_id = client.deploy_package(package_id, environment_id, notes=f"CI/CD deploy to {args.env}")
    print(f"Deployed package {package_id} to {args.env} (environment {environment_id}) -> deployment {deployment_id}")


if __name__ == "__main__":
    main()
