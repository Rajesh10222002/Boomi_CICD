"""
Thin wrapper over the Boomi AtomSphere Platform API.

Auth: Basic Auth with username "BOOMI_TOKEN.<login-email>" and the API
token value as the password (see README.md for how to generate one).
Reads credentials from environment variables so nothing secret ever
lives in a file that gets committed:

    BOOMI_ACCOUNT_ID
    BOOMI_USERNAME      (already in "BOOMI_TOKEN.<email>" form)
    BOOMI_API_TOKEN

Region: defaults to the US platform (api.boomi.com). If the trial
account is on the EU/GB platform, set BOOMI_BASE_URL to
"https://api.platform.gb.boomi.com" before running.
"""

import os
import time
import requests


class BoomiClient:
    def __init__(self):
        self.account_id = _require_env("BOOMI_ACCOUNT_ID")
        self.username = _require_env("BOOMI_USERNAME")
        self.token = _require_env("BOOMI_API_TOKEN")
        self.base_url = os.environ.get("BOOMI_BASE_URL", "https://api.boomi.com")
        self.session = requests.Session()
        self.session.auth = (self.username, self.token)
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    def _url(self, object_type, path=""):
        url = f"{self.base_url}/api/rest/v1/{self.account_id}/{object_type}"
        return f"{url}/{path}" if path else url

    def create_packaged_component(self, component_id, notes="CI build"):
        """Package a component version. Returns the new packageId."""
        body = {"componentId": component_id, "packageVersion": "", "notes": notes}
        resp = self.session.post(self._url("PackagedComponent"), json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["packageId"]

    def deploy_package(self, package_id, environment_id, notes="CI deploy"):
        """Deploy a packaged component to an environment. Returns the deploymentId."""
        body = {
            "packageId": package_id,
            "environmentId": environment_id,
            "notes": notes,
            "listenerStatus": "RUNNING",
        }
        resp = self.session.post(self._url("DeployedPackage"), json=body)
        resp.raise_for_status()
        return resp.json()["deploymentId"]

    def get_execution_record(self, execution_id):
        """Read one Execution Record (process run) by id."""
        resp = self.session.get(self._url("ExecutionRecord", "async", execution_id))
        resp.raise_for_status()
        return resp.json()

    def query_execution_records(self, process_id, environment_id, limit=5):
        """Find recent executions of a process in an environment."""
        query = {
            "QueryFilter": {
                "expression": {
                    "operator": "and",
                    "nestedExpression": [
                        {"argument": [process_id], "operator": "EQUALS", "property": "processId"},
                        {"argument": [environment_id], "operator": "EQUALS", "property": "environmentId"},
                    ],
                }
            }
        }
        resp = self.session.post(self._url("ExecutionRecord", "query"), json=query)
        resp.raise_for_status()
        results = resp.json().get("result", [])
        return results[:limit]

    def wait_for_execution(self, execution_id, timeout_s=180, poll_s=5):
        """Poll an execution until it leaves an in-progress state or timeout_s elapses."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            record = self.get_execution_record(execution_id)
            status = record.get("status")
            if status in ("COMPLETE", "ERROR", "ABORTED"):
                return record
            time.sleep(poll_s)
        raise TimeoutError(f"Execution {execution_id} did not finish within {timeout_s}s")


def _require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
