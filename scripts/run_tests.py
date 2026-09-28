"""
Run every tests/*.json test case against a deployed-to-Dev (usually) process
and check the result via the Boomi Execution Record API.

STATUS: scaffold. _trigger_process() and _check_expectation() are stubs —
see CLAUDE.md item 2. The trigger method needs to match however the real
pilot process is actually invoked (HTTP listener, scheduled, etc.), which
isn't known yet.

Usage:
    python scripts/run_tests.py --env dev --tests tests/
"""

import argparse
import glob
import json
import sys

import requests

from boomi_client import BoomiClient


def _trigger_process(test_case):
    """
    Kick off one execution of the process this test case targets.
    Placeholder assumes an HTTP listener; returns the raw response.
    Replace with the real trigger mechanism once known.
    """
    trigger = test_case["trigger"]
    if trigger["type"] != "http_post":
        raise NotImplementedError(f"Trigger type '{trigger['type']}' not implemented yet")
    listener_url = trigger["listener_url"]
    if listener_url.startswith("REPLACE-WITH"):
        raise SystemExit(f"Test case '{test_case['name']}' still has a placeholder listener_url.")
    resp = requests.post(listener_url, json=trigger["payload"], timeout=30)
    resp.raise_for_status()
    return resp


def _check_expectation(execution_record, expected):
    """
    Compare a finished Execution Record against a test case's `expected`
    block. Stub — fill in real field checks once the pilot process's
    actual Execution Record shape is known (e.g. document counts live
    under a different key for some connector types).
    """
    if execution_record.get("status") != expected.get("status"):
        return False, f"status was {execution_record.get('status')}, expected {expected.get('status')}"
    # TODO: check min_document_count and output_contains against the real
    # fields the Execution Record / Process Reporting API returns for this
    # process once it's chosen.
    return True, "ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--tests", required=True, help="Directory of test-case .json files")
    args = parser.parse_args()

    client = BoomiClient()
    test_files = sorted(glob.glob(f"{args.tests}/*.json"))
    if not test_files:
        print(f"No test files found in {args.tests}")
        sys.exit(1)

    failures = []
    for path in test_files:
        with open(path) as f:
            test_case = json.load(f)
        name = test_case["name"]
        print(f"Running test case: {name}")
        try:
            _trigger_process(test_case)
            # NOTE: in reality you'd capture the executionId the trigger
            # response or a subsequent query returns, then poll it:
            # record = client.wait_for_execution(execution_id)
            # For now this is left as a stub until the pilot process is
            # chosen and its actual response shape is known.
            print(f"  (stub) would poll execution and check expectations for '{name}'")
        except Exception as exc:  # noqa: BLE001 - surface any failure as a test failure
            failures.append((name, str(exc)))
            print(f"  FAILED: {exc}")

    if failures:
        print(f"\n{len(failures)} test case(s) failed:")
        for name, reason in failures:
            print(f"  - {name}: {reason}")
        sys.exit(1)

    print(f"\nAll {len(test_files)} test case(s) passed.")


if __name__ == "__main__":
    main()
