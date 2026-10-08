"""
Thin wrapper over the Boomi AtomSphere Platform API.

Auth: Basic Auth with username "BOOMI_TOKEN.<login-email>" and the API
token value as the password (see README.md for how to generate one).
Defaults to reading credentials from environment variables so nothing
secret ever lives in a file that gets committed (BoomiClient(...) also
accepts them as explicit kwargs, for callers with their own secrets store):

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
    def __init__(self, account_id=None, username=None, token=None, base_url=None, max_retries=3, backoff_factor=1.0):
        """
        Credentials default to the BOOMI_ACCOUNT_ID / BOOMI_USERNAME /
        BOOMI_API_TOKEN / BOOMI_BASE_URL env vars (what the CLI scripts and
        workflows use). Callers that keep credentials elsewhere — the
        Streamlit app reads them from st.secrets — can pass them in directly
        instead of exporting env vars.
        """
        self.account_id = account_id or _require_env("BOOMI_ACCOUNT_ID")
        self.username = username or _require_env("BOOMI_USERNAME")
        self.token = token or _require_env("BOOMI_API_TOKEN")
        self.base_url = base_url or os.environ.get("BOOMI_BASE_URL") or "https://api.boomi.com"
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

    def create_packaged_component(self, component_id, package_version="", notes="CI build"):
        """
        Package a component version, labeled with package_version (e.g.
        "v1.1") so it shows up as a meaningful version in Boomi's own UI
        instead of an opaque auto-generated one. Returns the new packageId.
        """
        body = {"componentId": component_id, "packageVersion": package_version, "notes": notes}
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

    def find_deployed_package(self, component_id, environment_id):
        """
        What Boomi itself says is live for a component in an environment
        (the active DeployedPackage), regardless of how it got there —
        including deploys done by hand in the Boomi UI that this repo's
        ledger never saw. Returns the most recent matching DeployedPackage
        dict (packageId, packageVersion, deployedDate, deployedBy, ...), or
        None.

        Queries by environment only (the one filter every account
        supports) and matches componentId client-side, paging through
        queryMore, rather than relying on server-side componentId/active
        filters.
        """
        query = {
            "QueryFilter": {
                "expression": {"operator": "EQUALS", "property": "environmentId", "argument": [environment_id]}
            }
        }
        resp = self._request("POST", self._url("DeployedPackage", "query"), json=query)
        data = resp.json()
        results = list(data.get("result", []))
        token = data.get("queryToken")
        while token:
            resp = self._request(
                "POST", self._url("DeployedPackage", "queryMore"), data=token, headers={"Content-Type": "text/plain"}
            )
            data = resp.json()
            results.extend(data.get("result", []))
            token = data.get("queryToken")
        matches = [
            r for r in results
            if r.get("componentId") == component_id and str(r.get("active", True)).lower() != "false"
        ]
        if not matches:
            return None
        matches.sort(key=lambda r: r.get("deployedDate", ""), reverse=True)
        return matches[0]

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

    def query_components(self, component_type="process", limit=200):
        """
        List live, current-version components of a given type (default
        "process") in the account, so a real component_id can be picked
        instead of a placeholder. Pages through queryMore until `limit` is
        reached or results run out.

        Filters deleted=false and currentVersion=true server-side (not just
        client-side on `deleted`) — otherwise a component with version
        history shows up once per past version, which both pollutes listings
        and makes find_component_by_name() see false "ambiguous" matches.
        """
        query = {
            "QueryFilter": {
                "expression": {
                    "operator": "and",
                    "nestedExpression": [
                        {"argument": [component_type], "operator": "EQUALS", "property": "type"},
                        {"argument": ["false"], "operator": "EQUALS", "property": "deleted"},
                        {"argument": ["true"], "operator": "EQUALS", "property": "currentVersion"},
                    ],
                }
            }
        }
        resp = self._request("POST", self._url("ComponentMetadata", "query"), json=query)
        data = resp.json()
        results = list(data.get("result", []))
        query_token = data.get("queryToken")
        while query_token and len(results) < limit:
            resp = self._request(
                "POST",
                self._url("ComponentMetadata", "queryMore"),
                data=query_token,
                headers={"Content-Type": "text/plain"},
            )
            data = resp.json()
            results.extend(data.get("result", []))
            query_token = data.get("queryToken")
        return results[:limit]

    def list_processes(self, component_type="process"):
        """Live {name, componentId} pairs for the given component type, for populating a picker."""
        return [
            {"name": c.get("name", ""), "componentId": c.get("componentId", "")}
            for c in self.query_components(component_type=component_type)
            if not c.get("deleted")
        ]

    def find_component_by_name(self, name, component_type="process"):
        """
        Resolve a live, current-version component's componentId by exact
        name + type, so workflows can take a process name instead of
        requiring a hand-copied componentId. Raises if there's no match, or
        more than one (component names aren't guaranteed unique in Boomi).

        Filters deleted=false and currentVersion=true server-side —
        otherwise a component with version history matches once per past
        version, and this raises a false "ambiguous" error for a component
        that's actually a single, unique process.
        """
        query = {
            "QueryFilter": {
                "expression": {
                    "operator": "and",
                    "nestedExpression": [
                        {"argument": [component_type], "operator": "EQUALS", "property": "type"},
                        {"argument": [name], "operator": "EQUALS", "property": "name"},
                        {"argument": ["false"], "operator": "EQUALS", "property": "deleted"},
                        {"argument": ["true"], "operator": "EQUALS", "property": "currentVersion"},
                    ],
                }
            }
        }
        resp = self._request("POST", self._url("ComponentMetadata", "query"), json=query)
        results = [r for r in resp.json().get("result", []) if not r.get("deleted")]
        if not results:
            raise LookupError(f"No live '{component_type}' component named '{name}' found in this account.")
        if len(results) > 1:
            ids = ", ".join(r["componentId"] for r in results)
            raise LookupError(
                f"Multiple '{component_type}' components named '{name}' found ({ids}) "
                "— pass an explicit component_id instead."
            )
        return results[0]["componentId"]

    def list_packages(self, component_id, limit=20):
        """Return recent PackagedComponent entries for a componentId, newest first."""
        query = {
            "QueryFilter": {
                "expression": {"operator": "EQUALS", "property": "componentId", "argument": [component_id]}
            }
        }
        resp = self._request("POST", self._url("PackagedComponent", "query"), json=query)
        results = resp.json().get("result", [])
        results.sort(key=lambda r: r.get("createdDate", ""), reverse=True)
        return results[:limit]

    def find_latest_package(self, component_id):
        """
        Most recently created PackagedComponent for this componentId, or
        None if it's never been packaged. Lets callers resolve "the package
        to promote" without anyone typing a packageId.
        """
        packages = self.list_packages(component_id, limit=1)
        return packages[0]["packageId"] if packages else None

    def get_component_xml(self, component_id, version=None):
        """
        Fetch the raw XML definition of a component: the current (latest)
        one by default, or a specific historical revision if `version` is
        given. The Component object returns XML, not JSON, so this
        overrides the session's default JSON Accept header for just this
        call.

        UNVERIFIED against a real account: version-pinned retrieval uses
        "{componentId}~{version}" as the path segment, based on Boomi's
        documented pattern for addressing a specific component revision —
        if a real call 404s or errors, that format may need correcting.
        """
        path = f"{component_id}~{version}" if version is not None else component_id
        resp = self._request("GET", self._url("Component", path), headers={"Accept": "application/xml"})
        return resp.text

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
