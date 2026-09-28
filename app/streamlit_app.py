"""
Boomi CI/CD admin console.

STATUS: scaffold. GitHub OAuth login (CLAUDE.md item 1) and the Actions-API
status/audit/dispatch calls (CLAUDE.md item 3) are both wired up below.
The process list and the package to promote both come live from the Boomi
API — nobody types a componentId or packageId anywhere in this UI.

Secrets this app expects (Streamlit Community Cloud "Secrets" panel, or
.streamlit/secrets.toml locally — never commit that file):

    GITHUB_OAUTH_CLIENT_ID
    GITHUB_OAUTH_CLIENT_SECRET
    GITHUB_OAUTH_REDIRECT_URI   this app's own URL, exactly as registered on
                                the GitHub OAuth App's "Authorization callback
                                URL" (e.g. the Streamlit Community Cloud URL)
    GITHUB_REPO                 e.g. "Rajesh10222002/Boomi_CICD"
    GITHUB_API_TOKEN            fine-grained PAT or GitHub App token, scoped to
                                this repo's Actions/Checks/Environments only
    ADMIN_USERNAMES             list of allowed GitHub usernames
    BOOMI_ACCOUNT_ID            same Boomi trial account as the GitHub secrets
    BOOMI_USERNAME              "BOOMI_TOKEN.<your-boomi-login-email>"
    BOOMI_API_TOKEN
    BOOMI_BASE_URL              optional — only if the account isn't on the
                                US platform, see scripts/boomi_client.py
"""

import secrets as secrets_lib
import sys
from pathlib import Path
from urllib.parse import urlencode

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from boomi_client import BoomiClient  # noqa: E402 - needs sys.path set up first

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_API_URL = "https://api.github.com/user"
GITHUB_API_BASE = "https://api.github.com"

_RUN_CONCLUSION_TO_STATUS = {"success": "passed", "failure": "failed", "cancelled": "cancelled"}


def _required_secret(name: str) -> str:
    value = st.secrets.get(name)
    if not value:
        st.error(f"Missing Streamlit secret: {name}. See app/.streamlit/secrets.toml.example.")
        st.stop()
    return value


def _admin_usernames() -> set:
    return {n.lower() for n in st.secrets.get("ADMIN_USERNAMES", [])}


def _login_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user",
        "state": state,
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


def _exchange_code_for_token(client_id: str, client_secret: str, redirect_uri: str, code: str) -> str:
    resp = requests.post(
        GITHUB_ACCESS_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"GitHub token exchange failed: {data}")
    return data["access_token"]


def _fetch_github_username(access_token: str) -> str:
    resp = requests.get(
        GITHUB_USER_API_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["login"]


def _check_authorized() -> bool:
    """
    GitHub OAuth login gated to usernames in st.secrets["ADMIN_USERNAMES"].
    Redirects through GitHub's authorize/access_token endpoints, fetches the
    authenticated username via GET https://api.github.com/user, and checks it
    against the allow-list. Login state lives in st.session_state, so it's
    re-checked on every fresh browser session (no persistent cookie/token).
    """
    if st.session_state.get("gh_authorized"):
        return True

    client_id = _required_secret("GITHUB_OAUTH_CLIENT_ID")
    client_secret = _required_secret("GITHUB_OAUTH_CLIENT_SECRET")
    redirect_uri = _required_secret("GITHUB_OAUTH_REDIRECT_URI")

    code = st.query_params.get("code")
    state = st.query_params.get("state")

    if code:
        if not state or state != st.session_state.get("oauth_state"):
            st.error("OAuth state mismatch — please try logging in again.")
            st.query_params.clear()
            st.stop()
        try:
            access_token = _exchange_code_for_token(client_id, client_secret, redirect_uri, code)
            username = _fetch_github_username(access_token)
        except Exception as exc:  # noqa: BLE001 - surface any auth failure to the user
            st.error(f"GitHub login failed: {exc}")
            st.query_params.clear()
            st.stop()

        st.query_params.clear()
        st.session_state["gh_username"] = username
        st.session_state["gh_authorized"] = username.lower() in _admin_usernames()
        st.rerun()

    state = secrets_lib.token_urlsafe(24)
    st.session_state["oauth_state"] = state
    st.link_button("Log in with GitHub", _login_url(client_id, redirect_uri, state))
    return False


@st.cache_resource
def _boomi_client() -> BoomiClient:
    return BoomiClient(
        account_id=_required_secret("BOOMI_ACCOUNT_ID"),
        username=_required_secret("BOOMI_USERNAME"),
        token=_required_secret("BOOMI_API_TOKEN"),
        base_url=st.secrets.get("BOOMI_BASE_URL") or None,
    )


@st.cache_data(ttl=60)
def _list_live_processes() -> list:
    """Live {name, componentId} pairs from Boomi, for the process picker."""
    try:
        return _boomi_client().list_processes()
    except Exception as exc:  # noqa: BLE001 - degrade to an empty list rather than crash the page
        st.error(f"Could not list Boomi processes: {exc}")
        return []


@st.cache_data(ttl=30)
def _find_latest_package(component_id: str):
    """The most recently created package for this process, or None if it's never been packaged."""
    try:
        return _boomi_client().find_latest_package(component_id)
    except Exception as exc:  # noqa: BLE001 - degrade to "no package found" rather than crash the page
        st.caption(f"Could not look up latest package: {exc}")
        return None


def _github_api_headers() -> dict:
    token = _required_secret("GITHUB_API_TOKEN")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


@st.cache_data(ttl=30)
def _get_latest_run_status(process_name: str) -> str:
    """
    Status of the most recent `ci.yml` run on `main`, mapped to a UI-friendly
    value ("passed" / "failed" / "running" / "cancelled" / "unknown").

    ci.yml currently builds/tests every process in components.json in one
    job rather than one process at a time, so this doesn't yet filter by
    process_name — it reports the same repo-wide CI status for every process
    until components.json / ci.yml support more than the one pilot process.
    """
    del process_name  # not yet filterable — see docstring
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{_required_secret('GITHUB_REPO')}/actions/workflows/ci.yml/runs",
            headers=_github_api_headers(),
            params={"branch": "main", "per_page": 1},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - degrade to "unknown" rather than crash the page
        st.caption(f"Could not fetch CI status: {exc}")
        return "unknown"

    runs = resp.json().get("workflow_runs", [])
    if not runs:
        return "unknown"
    run = runs[0]
    if run["status"] != "completed":
        return "running"
    return _RUN_CONCLUSION_TO_STATUS.get(run["conclusion"], run["conclusion"] or "unknown")


def _trigger_workflow(workflow_file: str, inputs: dict):
    """POST a workflow_dispatch event for workflow_file on main with the given inputs."""
    try:
        resp = requests.post(
            f"{GITHUB_API_BASE}/repos/{_required_secret('GITHUB_REPO')}/actions/workflows/{workflow_file}/dispatches",
            headers=_github_api_headers(),
            json={"ref": "main", "inputs": inputs},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - surface the failure instead of crashing the page
        st.error(f"Failed to trigger {workflow_file}: {exc}")
        return
    st.success(f"Triggered {workflow_file} with {inputs}")
    _get_latest_run_status.clear()


@st.cache_data(ttl=30)
def _get_recent_runs(limit: int = 15):
    """Most recent Actions runs across the repo (any workflow), newest first."""
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{_required_secret('GITHUB_REPO')}/actions/runs",
            headers=_github_api_headers(),
            params={"per_page": limit},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - degrade to an empty table rather than crash the page
        st.caption(f"Could not fetch run history: {exc}")
        return []
    return resp.json().get("workflow_runs", [])


def main():
    st.set_page_config(page_title="Boomi CI/CD Admin", layout="wide")

    if not _check_authorized():
        if st.session_state.get("gh_username"):
            st.error(f"GitHub user '{st.session_state['gh_username']}' is not on the admin allow-list.")
        else:
            st.info("Log in with GitHub to use this app.")
        st.stop()

    st.title("Boomi CI/CD Admin Console")
    st.caption(f"Signed in as {st.session_state['gh_username']}")

    processes = _list_live_processes()
    if not processes:
        st.warning("No live Boomi processes found (or the BOOMI_* secrets aren't configured yet).")
        st.stop()

    component_id_by_name = {p["name"]: p["componentId"] for p in processes}
    selected = st.multiselect(
        "Processes",
        options=sorted(component_id_by_name),
        help="Live process names from Boomi — pick one or more to build, test, or promote.",
    )

    for name in selected:
        component_id = component_id_by_name[name]
        st.subheader(name)
        status = _get_latest_run_status(name)
        st.write(f"Latest Dev test status: **{status}**")

        package_id = _find_latest_package(component_id)
        st.caption(f"Latest package: {package_id or 'none yet — build & deploy to Dev first'}")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button(f"Build & Deploy to Dev — {name}", key=f"dev-{name}"):
                _trigger_workflow("ci.yml", {"process_name": name, "component_id": component_id})
        with col2:
            qa_disabled = status != "passed" or not package_id
            if st.button(f"Promote to QA — {name}", key=f"qa-{name}", disabled=qa_disabled):
                _trigger_workflow(
                    "cd.yml", {"process_name": name, "package_id": package_id, "target_environment": "qa"}
                )
        with col3:
            # TODO: this should also require the QA stage's own status,
            # once _get_latest_run_status can distinguish stages.
            pd_disabled = status != "passed" or not package_id
            if st.button(f"Promote to PD — {name}", key=f"pd-{name}", disabled=pd_disabled):
                _trigger_workflow(
                    "cd.yml", {"process_name": name, "package_id": package_id, "target_environment": "prod"}
                )

    st.divider()
    st.subheader("Deploy history")
    runs = _get_recent_runs()
    if not runs:
        st.caption("No recent Actions runs found (or GITHUB_API_TOKEN / GITHUB_REPO not configured).")
    else:
        st.table(
            [
                {
                    "workflow": run["name"],
                    "who": run["triggering_actor"]["login"] if run.get("triggering_actor") else "-",
                    "when": run["created_at"],
                    "outcome": run["conclusion"] or run["status"],
                    "run_url": run["html_url"],
                }
                for run in runs
            ]
        )


if __name__ == "__main__":
    main()
