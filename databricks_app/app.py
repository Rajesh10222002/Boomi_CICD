"""
Boomi CI/CD admin console — Databricks App.

Same UI/behavior as the earlier Streamlit-Community-Cloud version (see git
history), minus the custom GitHub OAuth login: Databricks Apps are only
reachable by people granted access to this app in the workspace (Settings →
that app → Permissions), so there's no separate login flow to build or a
token to paste anywhere. This process/package picker and every GitHub
Actions call is otherwise unchanged — it still only *triggers* ci.yml/cd.yml
(via workflow_dispatch) rather than doing the Boomi deploy itself, so the
GitHub Issue tracking/audit trail those workflows create stays the audit
record.

Env vars this app needs (set as Databricks secret-scope references in
app.yaml, not literal values — see app.yaml and README.md):

    BOOMI_ACCOUNT_ID, BOOMI_USERNAME, BOOMI_API_TOKEN, BOOMI_BASE_URL (optional)
    GITHUB_API_TOKEN   fine-grained PAT scoped to this repo, Actions: write
    GITHUB_REPO        e.g. "Rajesh10222002/Boomi_CICD"
"""

import os

import requests
import streamlit as st

from boomi_client import BoomiClient

GITHUB_API_BASE = "https://api.github.com"
_RUN_CONCLUSION_TO_STATUS = {"success": "passed", "failure": "failed", "cancelled": "cancelled"}


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        st.error(f"Missing env var: {name}. See app.yaml / README.md for this app's required secrets.")
        st.stop()
    return value


@st.cache_resource
def _boomi_client() -> BoomiClient:
    return BoomiClient(
        account_id=_required_env("BOOMI_ACCOUNT_ID"),
        username=_required_env("BOOMI_USERNAME"),
        token=_required_env("BOOMI_API_TOKEN"),
        base_url=os.environ.get("BOOMI_BASE_URL") or None,
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
    token = _required_env("GITHUB_API_TOKEN")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


@st.cache_data(ttl=30)
def _get_latest_run_status(process_name: str) -> str:
    """
    Status of the most recent `ci.yml` run on `main`, mapped to a UI-friendly
    value ("passed" / "failed" / "running" / "cancelled" / "unknown").

    ci.yml doesn't tag runs per process in a way the Checks API exposes, so
    this reports the same repo-wide CI status for every process for now.
    """
    del process_name  # not yet filterable
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{_required_env('GITHUB_REPO')}/actions/workflows/ci.yml/runs",
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
            f"{GITHUB_API_BASE}/repos/{_required_env('GITHUB_REPO')}/actions/workflows/{workflow_file}/dispatches",
            headers=_github_api_headers(),
            json={"ref": "main", "inputs": inputs},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - surface the failure instead of crashing the page
        st.error(f"Failed to trigger {workflow_file}: {exc}")
        return
    st.success(f"Triggered {workflow_file} with {inputs}. Check the repo's Actions tab, or the tracking issue it opens, for progress.")
    _get_latest_run_status.clear()


@st.cache_data(ttl=30)
def _get_recent_runs(limit: int = 15):
    """Most recent Actions runs across the repo (any workflow), newest first."""
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/repos/{_required_env('GITHUB_REPO')}/actions/runs",
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
    st.title("Boomi CI/CD Admin Console")
    st.caption("Access to this page is controlled by Databricks App permissions, not a login on this page.")

    processes = _list_live_processes()
    if not processes:
        st.warning("No live Boomi processes found (or the BOOMI_* env vars aren't configured yet).")
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
