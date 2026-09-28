# Instructions for Claude Code

This repo is a scaffold for a Boomi CI/CD pilot: GitHub Actions packages,
tests, and promotes a Boomi integration process through **Dev → QA → PD**;
a Streamlit app is the single-click admin console that triggers it.

Read `README.md` first for the layout and one-time setup the human owner is
handling separately (Boomi API token, repo secrets, GitHub Environments,
GitHub OAuth App — **don't ask for these as plaintext, don't put them in any
file you commit**; they arrive as GitHub Actions secrets / Streamlit secrets,
added directly in those UIs).

## Context you don't have yet — ask before assuming

The human owner is still confirming these. If a task below needs one and it
isn't filled in yet, ask rather than inventing a value:
- Which Boomi process is the actual pilot for the **push-triggered** `ci.yml`
  pipeline (currently a placeholder id in `components/components.json`). Not
  needed for the app-driven flow — see below, that resolves processes live.
- The atom IDs for Dev, QA, PD (still placeholders in `environments/*.json`
  — the trial account's environments all show 0 runtimes, so there's no
  atom to attach yet). Environment IDs themselves are filled in.
- Whether PD promotion needs a required-reviewer approval in GitHub, beyond
  the app's own gate (currently assumed yes, environment `production` is
  referenced in `cd.yml`, but it's not created yet — that's a human step).

## What's already here (working)

- `scripts/boomi_client.py` — wrapper over the Boomi Platform API (Basic
  Auth via `BOOMI_TOKEN.<user>` + token; packaging, deployment, execution-
  record polling, live component listing, latest-package lookup). Retries
  429 (honoring `Retry-After`) and transient 5xx, raises `BoomiApiError`
  with the parsed response body otherwise. Base URL defaults to the US
  platform (`api.boomi.com`) — confirm the trial account is actually on
  that region before relying on it (`BOOMI_BASE_URL` overrides it).
  Credentials default to env vars but can be passed as kwargs (the
  Streamlit app does this, from `st.secrets`).
- `scripts/package_component.py`, `scripts/deploy_component.py`,
  `scripts/run_tests.py` — CLI entry points the workflows call.
- `scripts/list_processes.py` / `scripts/write_ad_hoc_config.py` — list live
  Boomi components by type, and write a one-process config for an ad-hoc
  run, so nothing requires a hand-maintained component_id.
- `.github/workflows/ci.yml` — on push to `main`: package → deploy to Dev →
  run tests, using `components/components.json`. Also accepts
  `workflow_dispatch` with `process_name` + `component_id` (what the
  Streamlit app sends) to package/deploy/test one ad-hoc process instead.
- `.github/workflows/cd.yml` — `workflow_dispatch` with inputs
  `process_name`, `package_id`, `target_environment` (qa|prod); deploys that
  package to the chosen environment (`prod` maps to the `production`
  GitHub Environment for its reviewer gate).
- `.github/workflows/list-processes.yml` — one-off `workflow_dispatch` to
  list live Boomi components in the Actions job summary, for manual lookup.
- `app/streamlit_app.py` — real GitHub OAuth login gated to
  `ADMIN_USERNAMES`; process list, CI status, workflow dispatch, and audit
  history all come from live GitHub Actions + Boomi API calls. Nobody types
  a componentId or packageId anywhere in the UI — the app resolves both via
  `BoomiClient.list_processes()` / `find_latest_package()`.

## What's still open

1. **Flesh out `scripts/run_tests.py`.** It currently assumes each test case
   in `tests/*.json` triggers the process via a simple HTTP POST to a listener
   URL and polls the Execution Record API for `COMPLETE`. Confirm this
   matches how the actual pilot process is triggered (it may be a different
   listener type) once that process is chosen, and fill in the real
   assertion logic (`_check_expectation()` is a stub).
2. **`_get_latest_run_status()` in the app doesn't filter by process** — every
   process shows the same repo-wide `ci.yml` status, because the workflow
   doesn't tag runs per process in a way the Checks API exposes yet. Worth
   revisiting once there's more than one process actually flowing through
   `ci.yml` at once.

## Ground rules

- Never commit secrets, tokens, or `.streamlit/secrets.toml` — `.gitignore`
  already excludes it, keep it that way.
- Every script should run locally with env vars for testing
  (`BOOMI_ACCOUNT_ID`, `BOOMI_USERNAME`, `BOOMI_API_TOKEN`) before it's
  wired into a workflow — don't make the GitHub Actions run the only way to
  test a change.
- Keep `dev`/`qa`/`prod` handling symmetric in the scripts — no special-casing
  one environment's code path, only its config values.
