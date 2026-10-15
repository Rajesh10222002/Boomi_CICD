"""
Deployment history of one process in one environment, straight from Boomi
(DeployedPackage records) — not from deployments/ledger.csv, so deployments
done by hand in the Boomi UI show up too.

Boomi is asked for every DeployedPackage record of the environment, which is
filtered client-side to the process's componentId. Records are kept whether
or not they are still active, so previous deployments appear as history. The
report also lists the packages that were built for the process but have no
deployment record in this environment (not available to roll back to without
an explicit override).

Read-only: never deploys anything.

Prints `component-id=`, `current-package-id=`, `record-count=` and
`active-count=` on stdout (for $GITHUB_OUTPUT); the markdown report is written
to --out and the diagnostics go to stderr.

Usage:
    python scripts/deployment_history.py --process-name "My Process" --environment dev
    python scripts/deployment_history.py --process-name "My Process" --component-id X --environment qa --out history.md
"""

import argparse
import json
import os
import sys

from boomi_client import BoomiClient

_ENV_DIR = os.path.join(os.path.dirname(__file__), "..", "environments")


def environment_id(environment):
    with open(os.path.join(_ENV_DIR, f"{environment}.json"), encoding="utf-8") as f:
        return json.load(f)["environment_id"]


def _is_active(record):
    return str(record.get("active", True)).lower() != "false"


def fetch_history(client, component_id, env_id):
    """DeployedPackage records of this component in this environment, newest first (active or not)."""
    records = [r for r in client.list_deployed_packages(env_id) if r.get("componentId") == component_id]
    records.sort(key=lambda r: r.get("deployedDate", ""), reverse=True)
    return records


def current_record(records):
    active = [r for r in records if _is_active(r)]
    return active[0] if active else None


def _cell(value):
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def render_markdown(process_name, environment, records, never_deployed):
    current = current_record(records)
    current_pkg = current.get("packageId") if current else None
    lines = [
        f"## 📜 Deployment history — `{process_name}` in **{environment}**",
        "",
        f"Source: Boomi DeployedPackage records ({len(records)} found, "
        f"{sum(_is_active(r) for r in records)} active). Includes deployments made by hand in the Boomi UI.",
        "",
    ]
    if not records:
        lines += ["> ⚠️ Boomi reports no deployment of this process in this environment."]
    else:
        lines += [
            "| # | Version | Package ID | Deployed (UTC) | Deployed by | Status | Notes |",
            "|--:|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(records, 1):
            is_current = r is current
            status = "🟢 **CURRENT**" if is_current else ("active" if _is_active(r) else "previous")
            lines.append(
                f"| {i} | {_cell(r.get('packageVersion'))} | `{_cell(r.get('packageId'))}` | "
                f"{_cell(r.get('deployedDate'))} | {_cell(r.get('deployedBy'))} | {status} | {_cell(r.get('notes'))} |"
            )
        if len(records) == 1:
            lines += [
                "",
                "> ℹ️ Boomi returned only one deployment record. Either this process has only ever been deployed once "
                "here, or Boomi only keeps the active deployment on this account — in which case previous versions "
                "can't be listed and a rollback needs the *allow_undeployed* override.",
            ]
    if never_deployed:
        lines += ["", f"### 📦 Packages built for this process with no deployment record in {environment}", "",
                  "| Version | Package ID | Created (UTC) |", "|---|---|---|"]
        for p in never_deployed:
            lines.append(f"| {_cell(p.get('packageVersion'))} | `{_cell(p.get('packageId'))}` | {_cell(p.get('createdDate'))} |")
    if len(records) > 1 or (records and current_pkg is None):
        lines += [
            "",
            "### ↩️ To roll back",
            "",
            f"Run the **Rollback** workflow with environment **{environment}**, this process name, and the **Package ID** "
            "of the row you want from the table above (not the CURRENT one).",
        ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", required=True)
    parser.add_argument("--component-id", default="", help="skip the name lookup (needed if the name is ambiguous)")
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--out", default="deployment_history.md")
    args = parser.parse_args()

    client = BoomiClient()
    component_id = args.component_id or client.find_component_by_name(args.process_name)
    env_id = environment_id(args.environment)

    records = fetch_history(client, component_id, env_id)
    deployed_ids = {r.get("packageId") for r in records}
    try:
        packages = client.list_packages(component_id, limit=200)
    except Exception as exc:  # the history above is still useful without this section
        print(f"Could not list packages for {component_id}: {exc}", file=sys.stderr)
        packages = []
    never_deployed = [p for p in packages if p.get("packageId") not in deployed_ids]

    if records:
        print(f"DeployedPackage fields seen: {sorted(records[0].keys())}", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render_markdown(args.process_name, args.environment, records, never_deployed))

    current = current_record(records)
    print(f"component-id={component_id}")
    print(f"current-package-id={current.get('packageId') if current else ''}")
    print(f"record-count={len(records)}")
    print(f"active-count={sum(_is_active(r) for r in records)}")


if __name__ == "__main__":
    main()
