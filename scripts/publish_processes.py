"""
Snapshot live Boomi processes (+ each one's latest package, if any) to a
JSON file for the GitHub Pages admin console (docs/index.html) to read.
The page never talks to Boomi directly — this script does it server-side,
using the same BOOMI_* secrets as the other workflows, and the page just
fetches the resulting static file.

Usage:
    python scripts/publish_processes.py --out ../docs/processes.json
"""

import argparse
import datetime
import json

from boomi_client import BoomiClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="Where to write the processes.json snapshot")
    args = parser.parse_args()

    client = BoomiClient()
    processes = client.list_processes()
    for p in processes:
        p["latestPackageId"] = client.find_latest_package(p["componentId"])

    snapshot = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "processes": processes,
    }
    with open(args.out, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"Wrote {len(processes)} process(es) to {args.out}")


if __name__ == "__main__":
    main()
