"""
Record one successful deploy into deployments/ledger.csv (append) and
deployments/current.csv (upsert by process_name+environment) — the
repo's own record of what's actually deployed where, independent of the
Boomi API. See deployments/README.md for what each file is for.

Only edits the two files on disk — the calling workflow step is
responsible for committing/pushing them.

--component-id and --version are optional: if omitted, they're looked up
from the most recent ledger row that already recorded this same
package_id.

Usage:
    python scripts/update_deployment_ledger.py \
        --process-name "My Process" --component-id abc-123 --environment dev \
        --package-id pkg-123 --version v1.1 --action ci-deploy --status success \
        --requested-by someone --run-url https://github.com/.../actions/runs/1 \
        [--comment "..."]
"""

import argparse
import csv
import datetime
import os

_HERE = os.path.dirname(__file__)
LEDGER_PATH = os.path.join(_HERE, "..", "deployments", "ledger.csv")
CURRENT_PATH = os.path.join(_HERE, "..", "deployments", "current.csv")

LEDGER_FIELDS = [
    "timestamp",
    "process_name",
    "component_id",
    "environment",
    "package_id",
    "version",
    "action",
    "requested_by",
    "run_url",
    "comment",
    "status",
]
CURRENT_FIELDS = [
    "process_name",
    "component_id",
    "environment",
    "package_id",
    "version",
    "deployed_at",
    "deployed_by",
    "run_url",
]


def _lookup_metadata_by_package_id(package_id):
    """Most recent ledger row's (component_id, version) for this package_id, or ("", "") if none."""
    if not package_id or not os.path.exists(LEDGER_PATH):
        return "", ""
    with open(LEDGER_PATH, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["package_id"] == package_id]
    if not rows:
        return "", ""
    latest = rows[-1]  # ledger.csv is append-only, so the last match is the most recent
    return latest.get("component_id", ""), latest.get("version", "")


def _append_ledger_row(row):
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    is_new = not os.path.exists(LEDGER_PATH)
    with open(LEDGER_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _upsert_current_row(row):
    os.makedirs(os.path.dirname(CURRENT_PATH), exist_ok=True)
    rows = []
    if os.path.exists(CURRENT_PATH):
        with open(CURRENT_PATH, newline="") as f:
            rows = list(csv.DictReader(f))
    key = (row["process_name"], row["environment"])
    rows = [r for r in rows if (r["process_name"], r["environment"]) != key]
    rows.append(row)
    rows.sort(key=lambda r: (r["process_name"], r["environment"]))
    with open(CURRENT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CURRENT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", required=True)
    parser.add_argument("--component-id", default="")
    parser.add_argument("--environment", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--version", default="")
    parser.add_argument("--action", required=True, choices=["ci-deploy", "promote"])
    parser.add_argument("--status", required=True, choices=["success", "failure"])
    parser.add_argument("--requested-by", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--comment", default="")
    args = parser.parse_args()

    component_id = args.component_id
    version = args.version
    if not component_id or not version:
        looked_up_component_id, looked_up_version = _lookup_metadata_by_package_id(args.package_id)
        component_id = component_id or looked_up_component_id
        version = version or looked_up_version

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    _append_ledger_row(
        {
            "timestamp": timestamp,
            "process_name": args.process_name,
            "component_id": component_id,
            "environment": args.environment,
            "package_id": args.package_id,
            "version": version,
            "action": args.action,
            "requested_by": args.requested_by,
            "run_url": args.run_url,
            "comment": args.comment,
            "status": args.status,
        }
    )

    if args.status == "success":
        _upsert_current_row(
            {
                "process_name": args.process_name,
                "component_id": component_id,
                "environment": args.environment,
                "package_id": args.package_id,
                "version": version,
                "deployed_at": timestamp,
                "deployed_by": args.requested_by,
                "run_url": args.run_url,
            }
        )


if __name__ == "__main__":
    main()
