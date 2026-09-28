# Instructions for Claude Code

This repo is a scaffold for a Boomi CI/CD pilot: GitHub Actions packages,
tests, and promotes a Boomi integration process through **Dev → QA → PD**;
a static GitHub Pages admin console (`docs/index.html`) is the single-click
UI that triggers it — no Streamlit, no third-party hosting (see README.md's
"Why no Streamlit" section for why that was ruled out: compliance/credential-
custody concerns with a third-party-hosted admin UI).

Read `README.md` first for the layout and one-time setup the human owner is
handling separately (Boomi API token, repo secrets, GitHub Environments,
GitHub Pages — **don't ask for these as plaintext, don't put them in any
file you commit**; they arrive as GitHub Actions secrets, added directly in
that UI, or as a personal access token each admin pastes into the page
themselves, kept only in their own browser's local storage).

## Context you don't have yet — ask before assuming

The human owner is still confirming these. If a task below needs one and it
isn't filled in yet, ask rather than inventing a value:
- Which Boomi process is the actual pilot for the **push-triggered** `ci.yml`
  pipeline (currently a placeholder id in `components/components.json`). Not
  needed for the admin-console flow — see below, that resolves processes live.
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
  Credentials default to env vars but can be passed as kwargs.
- `scripts/package_component.py`, `scripts/deploy_component.py`,
  `scripts/run_tests.py` — CLI entry points the workflows call.
- `scripts/list_processes.py` / `scripts/write_ad_hoc_config.py` — list live
  Boomi components by type, and write a one-process config for an ad-hoc
  run, so nothing requires a hand-maintained component_id.
- `scripts/publish_processes.py` — snapshots live processes + each one's
  latest package to a JSON file, for the admin console to read.
- `.github/workflows/ci.yml` — on push to `main`: package → deploy to Dev →
  run tests, using `components/components.json`. Also accepts
  `workflow_dispatch` with `process_name` + `component_id` (what the admin
  console sends) to package/deploy/test one ad-hoc process instead.
- `.github/workflows/cd.yml` — `workflow_dispatch` with inputs
  `process_name`, `package_id`, `target_environment` (qa|prod); deploys that
  package to the chosen environment (`prod` maps to the `production`
  GitHub Environment for its reviewer gate).
- `.github/workflows/publish-processes.yml` — runs `publish_processes.py` on
  a 15-minute schedule + `workflow_dispatch`, commits `docs/processes.json`
  if it changed. This is the only thing that ever holds a Boomi credential
  in this whole admin-console flow — the static page never calls Boomi.
- `.github/workflows/list-processes.yml` — one-off `workflow_dispatch` to
  list live Boomi components in the Actions job summary, for manual lookup.
- `.github/workflows/debug-secrets.yml` — prints SHA256 hashes of the Boomi
  secrets (never the values), to sanity-check what got saved.
- `docs/index.html` — the admin console. Fetches `docs/processes.json` for
  an instant client-side-searchable process list; each admin pastes their
  own fine-grained GitHub PAT (kept in that browser's `localStorage` only)
  to call GitHub's REST API directly for CI status, run history, and
  triggering `ci.yml`/`cd.yml`. Nobody types a componentId or packageId
  anywhere in the UI.

## What's still open

1. **Flesh out `scripts/run_tests.py`.** It currently assumes each test case
   in `tests/*.json` triggers the process via a simple HTTP POST to a listener
   URL and polls the Execution Record API for `COMPLETE`. Confirm this
   matches how the actual pilot process is triggered (it may be a different
   listener type) once that process is chosen, and fill in the real
   assertion logic (`_check_expectation()` is a stub).
2. **The CI status shown per process isn't actually per-process** — every
   process shows the same repo-wide `ci.yml` status, because the workflow
   doesn't tag runs per process in a way the Checks API exposes yet. Worth
   revisiting once there's more than one process actually flowing through
   `ci.yml` at once.
3. **GitHub Pages needs to be enabled once** (Settings → Pages → branch
   `main`, folder `/docs`) and `publish-processes.yml` needs to run at least
   once — both human steps, see README.md.

## Ground rules

- Never commit secrets or tokens. `docs/processes.json` is fine to commit
  (it's just names/IDs, not credentials) — the actual Boomi/GitHub tokens
  never touch a file in this repo.
- Every script should run locally with env vars for testing
  (`BOOMI_ACCOUNT_ID`, `BOOMI_USERNAME`, `BOOMI_API_TOKEN`) before it's
  wired into a workflow — don't make the GitHub Actions run the only way to
  test a change.
- Keep `dev`/`qa`/`prod` handling symmetric in the scripts — no special-casing
  one environment's code path, only its config values.
- `docs/index.html` is deliberately dependency-free vanilla HTML/JS (no
  build step, no framework, no CDN) so it stays simple to read and edit —
  don't introduce a bundler or framework for it without a real reason to.
