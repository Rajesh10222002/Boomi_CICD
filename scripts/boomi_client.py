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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class BoomiApiError(RuntimeError):
    """
    Raised for any non-2xx Boomi Platform API response, after retries are
    exhausted. Carries the parsed error so callers can branch on it (e.g. a
    trial account's "feature not enabled" 403s surface a specific message
    here rather than a generic HTTPError).
    """

    def __init__(self, status_code, message, response_body=None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"Boomi API error {status_code}: {message}")


class BoomiClient:
    def __init__(self, max_retries=3, backoff_factor=1.0):
        self.account_id = _require_env("BOOMI_ACCOUNT_ID")
        self.username = _require_env("BOOMI_USERNAME")
        self.token = _require_env("BOOMI_API_TOKEN")
        self.base_url = os.environ.get("BOOMI_BASE_URL", "https://api.boomi.com")
        self.session = requests.Session()
        self.session.auth = (self.username, self.token)
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

        # Connection-level retries: transient 5xx / dropped connections.
        # Rate limiting (429) is handled separately in _request() because it
        # needs to honor the Retry-After header, which Retry(status_forcelist=...)
        # already does on modern urllib3 — but we retry it explicitly below too
        # so this keeps working across urllib3 versions that don't.
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._max_retries = max_retries

    def _url(self, object_type, path=""):
        url = f"{self.base_url}/api/rest/v1/{self.account_id}/{object_type}"
        return f"{url}/{path}" if path else url

    def _request(self, method, url, **kwargs):
        """
        Send a request, retrying on 429 (honoring Retry-After) on top of the
        session's connection-level retries, then raise BoomiApiError with the
        parsed response body for any remaining non-2xx result.
        """
        attempt = 0
        while True:
            resp = self.session.request(method, url, **kwargs)
            if resp.status_code == 429 and attempt < self._max_retries:
                wait_s = _parse_retry_after(resp.headers.get("Retry-After")) or (2**attempt)
                time.sleep(wait_s)
                attempt += 1
                continue
            break

        if not resp.ok:
            self._raise_for_error(resp)
        return resp

    @staticmethod
    def _raise_for_error(resp):
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        message = body
        if isinstance(body, dict):
            message = body.get("message") or body.get("errorMessage") or body
        raise BoomiApiError(resp.status_code, message, response_body=body)

    def create_packaged_component(self, component_id, notes="CI build"):
        """Package a component version. Returns the new packageId."""
        body = {"componentId": component_id, "packageVersion": "", "notes": notes}
        resp = self._request("POST", self._url("PackagedComponent"), json=body)
        return resp.json()["packageId"]

    def deploy_package(self, package_id, environment_id, notes="CI deploy"):
        """Deploy a packaged component to an environment. Returns the deploymentId."""
        body = {
            "packageId": package_id,
            "environmentId": environment_id,
            "notes": notes,
            "listenerStatus": "RUNNING",
        }
        resp = self._request("POST", self._url("DeployedPackage"), json=body)
        return resp.json()["deploymentId"]

    def get_execution_record(self, execution_id):
        """Read one Execution Record (process run) by id."""
        resp = self._request("GET", self._url("ExecutionRecord", "async", execution_id))
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
        resp = self._request("POST", self._url("ExecutionRecord", "query"), json=query)
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


def _parse_retry_after(value):
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
