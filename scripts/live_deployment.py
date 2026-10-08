"""
Fallback lookup of what's live in an environment according to Boomi itself,
for processes deployed by hand in the Boomi UI (so deployments/current.csv
has no row for them). The ledger stays the first source of truth; this is
only consulted when it has nothing for a process+environment.
"""

import json
import os
import sys

from boomi_client import BoomiClient

_ENV_DIR = os.path.join(os.path.dirname(__file__), "..", "environments")


def _environment_id(environment):
    with open(os.path.join(_ENV_DIR, f"{environment}.json"), encoding="utf-8") as f:
        return json.load(f)["environment_id"]


def lookup(name, environment):
    """(package_id, component_id, version, deployed_date, deployed_by) from Boomi, or None. Never raises on API trouble."""
    try:
        client = BoomiClient()
        component_id = client.find_component_by_name(name)
        dep = client.find_deployed_package(component_id, _environment_id(environment))
    except Exception as exc:
        print(f"Boomi live-deployment lookup failed for '{name}' in {environment}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    if not dep:
        print(f"Boomi reports no active deployment of '{name}' ({component_id}) in {environment}.", file=sys.stderr)
        return None
    return (
        dep.get("packageId", ""),
        component_id,
        dep.get("packageVersion", ""),
        dep.get("deployedDate", ""),
        dep.get("deployedBy", ""),
    )
