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
- Which Boomi process is the actual pilot (currently a placeholder id in
  `components/components.json`).
- The real environment IDs / atom IDs for Dev, QA, PD (placeholders in
  `environments/*.json`).
- The admin allow-list (GitHub usernames allowed into the Streamlit app).
- Whether PD promotion needs a required-reviewer approval in GitHub, beyond
  the app's own gate (currently assumed yes, environment `production` is
  referenced in `cd.yml`, but it's not created yet — that's a human step).

## What's already here (working, but against placeholders)

- `scripts/boomi_client.py` — thin wrapper over the Boomi Platform API
  (Basic Auth via `BOOMI_TOKEN.<user>` + token, packaging, deployment,
  execution-record polling). Base URL defaults to the US platform
  (`api.boomi.com`) — check whether the trial account is actually on that
  region before relying on it.
- `scripts/package_component.py`, `scripts/deploy_component.py`,
  `scripts/run_tests.py` — CLI entry points the workflows call.
- `.github/workflows/ci.yml` — on push to `main`: package → deploy to Dev →
  run tests → publish a pass/fail check.
- `.github/workflows/cd.yml` — `workflow_dispatch` with inputs
  `process_id`, `package_version`, `target_environment` (dev|qa|prod);
  deploys that package to the chosen environment.
- `app/streamlit_app.py` — process list (from `components/components.json`),
  per-process CI status pulled from the GitHub Checks API, and three buttons
  (Build & Deploy to Dev / Promote to QA / Promote to PD) that call
  `workflow_dispatch`. GitHub OAuth login is stubbed — **not wired up yet**.

## Priority order for what to build out next

1. **Finish the GitHub OAuth login in `app/streamlit_app.py`.** Currently a
   stub (`_check_authorized()` always returns `True` — replace it). Use the
   OAuth Client ID/Secret from Streamlit secrets, redirect through GitHub's
   `authorize`/`access_token` endpoints, fetch the authenticated username via
   `GET https://api.github.com/user`, and check it against `ADMIN_USERNAMES`
   (a list, also read from Streamlit secrets — not hardcoded in the file).
2. **Flesh out `scripts/run_tests.py`.** It currently assumes each test case
   in `tests/*.json` triggers the process via a simple HTTP POST to a listener
   URL and polls the Execution Record API for `COMPLETE`. Confirm this
   matches how the actual pilot process is triggered (it may be a different
   listener type) once that process is chosen, and fill in the real
   assertion logic (`_check_expectation()` is a stub).
3. **Wire the Streamlit app's status/audit views to real GitHub Actions data**
   — `_get_latest_run_status()` and the audit-log table are stubbed with
   placeholder data; replace with real calls to the Actions/Checks REST API
   using a GitHub App or fine-grained PAT (read from Streamlit secrets, not
   committed).
4. **Add error handling and retries** to `boomi_client.py` — trial-account
   API responses may include rate-limit or feature-not-enabled errors that
   aren't handled yet.

## Ground rules

- Never commit secrets, tokens, or `.streamlit/secrets.toml` — `.gitignore`
  already excludes it, keep it that way.
- Every script should run locally with env vars for testing
  (`BOOMI_ACCOUNT_ID`, `BOOMI_USERNAME`, `BOOMI_API_TOKEN`) before it's
  wired into a workflow — don't make the GitHub Actions run the only way to
  test a change.
- Keep `dev`/`qa`/`prod` handling symmetric in the scripts — no special-casing
  one environment's code path, only its config values.
