"""
Boomi CI/CD admin console.

STATUS: scaffold. GitHub OAuth login and real Actions-API status calls are
stubbed — see CLAUDE.md items 1 and 3. This runs and renders, but everyone
is treated as authorized and status/audit data is fake, until those are
filled in.

Secrets this app expects (Streamlit Community Cloud "Secrets" panel, or
.streamlit/secrets.toml locally — never commit that file):

    GITHUB_OAUTH_CLIENT_ID
    GITHUB_OAUTH_CLIENT_SECRET
    GITHUB_REPO              e.g. "Rajesh10222002/Boomi_CICD"
    GITHUB_API_TOKEN         fine-grained PAT or GitHub App token, scoped to
                             this repo's Actions/Checks/Environments only
    ADMIN_USERNAMES          list of allowed GitHub usernames
"""

import json
from pathlib import Path

import streamlit as st

COMPONENTS_PATH = Path(__file__).parent.parent / "components" / "components.json"


def _check_authorized() -> bool:
    """
    TODO (CLAUDE.md item 1): replace with real GitHub OAuth.
    Should redirect through GitHub's authorize/access_token endpoints,
    fetch the username via GET https://api.github.com/user, and check it
    against st.secrets["ADMIN_USERNAMES"]. Until then, everyone is let in
    so the rest of the UI can be built and demoed.
    """
    st.warning("Auth not wired up yet — anyone can use this build. See CLAUDE.md item 1.", icon="⚠️")
    return True


def _load_processes():
    with open(COMPONENTS_PATH) as f:
        return json.load(f)["processes"]


def _get_latest_run_status(process_name: str) -> str:
    """
    TODO (CLAUDE.md item 3): replace with a real call to the GitHub
    Checks/Actions API for the most recent ci.yml run touching this
    process. Placeholder always returns "unknown" so the UI doesn't lie
    about test results.
    """
    return "unknown"


def _trigger_workflow(workflow_file: str, inputs: dict):
    """
    TODO (CLAUDE.md item 3): POST to
    https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches
    with {"ref": "main", "inputs": inputs}, using st.secrets["GITHUB_API_TOKEN"].
    """
    st.info(f"(stub) would trigger {workflow_file} with {inputs}")


def main():
    st.set_page_config(page_title="Boomi CI/CD Admin", layout="wide")

    if not _check_authorized():
        st.error("You are not authorized to use this app.")
        st.stop()

    st.title("Boomi CI/CD Admin Console")

    processes = _load_processes()
    selected = st.multiselect(
        "Processes",
        options=[p["name"] for p in processes],
        help="Pick one or more processes to build, test, or promote.",
    )

    for name in selected:
        st.subheader(name)
        status = _get_latest_run_status(name)
        st.write(f"Latest Dev test status: **{status}**")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button(f"Build & Deploy to Dev — {name}", key=f"dev-{name}"):
                _trigger_workflow("ci.yml", {"process_name": name})
        with col2:
            qa_disabled = status != "passed"
            if st.button(f"Promote to QA — {name}", key=f"qa-{name}", disabled=qa_disabled):
                _trigger_workflow("cd.yml", {"process_name": name, "target_environment": "qa"})
        with col3:
            # TODO: this should also require the QA stage's own status,
            # once _get_latest_run_status can distinguish stages.
            pd_disabled = status != "passed"
            if st.button(f"Promote to PD — {name}", key=f"pd-{name}", disabled=pd_disabled):
                _trigger_workflow("cd.yml", {"process_name": name, "target_environment": "prod"})

    st.divider()
    st.subheader("Deploy history")
    st.caption("TODO: pull real past Actions runs instead of showing this placeholder.")
    st.table(
        [
            {"process": "PLACEHOLDER-sample-process", "environment": "dev", "who": "-", "when": "-", "outcome": "-"},
        ]
    )


if __name__ == "__main__":
    main()
