"""
Validate and resolve a rollback request: put <package-id> back as the active
deployment of <process> in <environment>.

Checks, against Boomi's own DeployedPackage history (see
deployment_history.py), that:
  * the process exists and is currently deployed in the environment,
  * the package was previously deployed there (or, with --allow-undeployed,
    at least belongs to this process — an explicit override for when Boomi
    doesn't expose older deployment records),
  * the package is not already the one that's live.
It fails (non-zero exit, message on stderr) otherwise, so nothing gets deployed.

Prints, for $GITHUB_OUTPUT: component-id, package-id, version,
target-deployed (date it was last live here, or "never recorded"),
current-package-id, current-version, current (one-line description) and
source (history | override).

Usage:
    python scripts/resolve_rollback_target.py --name "My Process" --environment dev --package-id abc
"""

import argparse
import sys

from boomi_client import BoomiClient
from deployment_history import current_record, environment_id, fetch_history


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--allow-undeployed", action="store_true")
    args = parser.parse_args()
    package_id = args.package_id.strip()

    client = BoomiClient()
    component_id = client.find_component_by_name(args.name)
    records = fetch_history(client, component_id, environment_id(args.environment))

    current = current_record(records)
    if current is None:
        raise SystemExit(
            f"'{args.name}' is not currently deployed in '{args.environment}' according to Boomi — "
            "there is nothing to roll back from."
        )
    if current.get("packageId") == package_id:
        raise SystemExit(
            f"Package {package_id} is already the live deployment of '{args.name}' in '{args.environment}' — nothing to do."
        )

    matches = [r for r in records if r.get("packageId") == package_id]
    if matches:
        version, deployed, source = matches[0].get("packageVersion", ""), matches[0].get("deployedDate", ""), "history"
    else:
        available = ", ".join(
            f"{r.get('packageVersion') or '?'} = {r.get('packageId')}" for r in records if r is not current
        ) or "none — Boomi lists no earlier deployment"
        if not args.allow_undeployed:
            raise SystemExit(
                f"Package {package_id} has no deployment record for '{args.name}' in '{args.environment}'.\n"
                f"Earlier deployments Boomi knows about: {available}\n"
                "Run 'Deployment history' to see them, or re-run with allow_undeployed to deploy a package "
                "that was built for this process but never recorded as deployed here."
            )
        package = next((p for p in client.list_packages(component_id, limit=200) if p.get("packageId") == package_id), None)
        if package is None:
            raise SystemExit(f"Package {package_id} does not belong to '{args.name}' ({component_id}) — refusing to deploy it.")
        version, deployed, source = package.get("packageVersion", ""), "never recorded", "override"
        print(f"WARNING: package {package_id} has no deployment record in {args.environment}; deploying it by override.",
              file=sys.stderr)

    cur_version = current.get("packageVersion") or "(no version label)"
    print(f"component-id={component_id}")
    print(f"package-id={package_id}")
    print(f"version={version}")
    print(f"target-deployed={deployed}")
    print(f"current-package-id={current.get('packageId')}")
    print(f"current-version={cur_version}")
    print(f"current={cur_version} — package {current.get('packageId')}, deployed {current.get('deployedDate', '?')}"
          f"{' by ' + current['deployedBy'] if current.get('deployedBy') else ''}")
    print(f"source={source}")


if __name__ == "__main__":
    main()
