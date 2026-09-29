# Instructions for Claude Code

This repo is a scaffold for a Boomi CI/CD pilot: GitHub Actions packages,
tests, and promotes a Boomi integration process through **Dev → QA → PD**.
There is no custom admin app — `workflow_dispatch` (Actions tab → "Run
workflow") is the trigger, and each manual run opens (then closes) a GitHub
Issue as its audit record. Three admin-UI approaches were tried and
dropped; see README.md's "Why no custom admin app" for why (Streamlit
Community Cloud: third-party credential custody; a static GitHub Pages
page with a pasted PAT: no central token visibility/revocation; a
Databricks App: decided against to keep everything in one place with
nothing extra to deploy/maintain). This settled on matching how Quanta's
other Boomi/Incorta CI/CD repos (`qco-incorta-cicd`, `qco-incorta-ui`)
already do it in production. Don't reintroduce a custom admin app without
being asked to.

Read `README.md` first for the layout and one-time setup the human owner is
handling separately (Boomi API token, repo secrets, GitHub Environments —
**don't ask for these as plaintext, don't put them in any file you commit**;
they arrive as GitHub Actions secrets, added directly in that UI).

## Context you don't have yet — ask before assuming

The human owner is still confirming these. If a task below needs one and it
isn't filled in yet, ask rather than inventing a value:
- Which Boomi process is the actual pilot for the **components.json-driven**
  `ci.yml` run (leave both `process_name`/`component_id` blank to use it —
  currently a placeholder id in `components/components.json`, so that path
  fails by design until this is filled in). Not needed for the normal
  per-process `workflow_dispatch` run — `process_name` alone resolves
  componentId/packageId live via `BoomiClient.find_component_by_name()` /
  `find_latest_package()`.
- The atom IDs for Dev, QA, PD (still placeholders in `environments/*.json`
  — the trial account's environments all show 0 runtimes, so there's no
  atom to attach yet). Environment IDs themselves are filled in.
- Whether PD promotion needs a required-reviewer approval in GitHub (assumed
  yes, environment `production` is referenced in `cd.yml`, but it's not
  created yet — that's a human step).

## What's already here (working)

- `scripts/boomi_client.py` — wrapper over the Boomi Platform API (Basic
  Auth via `BOOMI_TOKEN.<user>` + token; packaging, deployment, execution-
  record polling, live component listing/lookup-by-name, package
  listing/latest-package lookup). Retries 429 (honoring `Retry-After`) and
  transient 5xx, raises `BoomiApiError` with the parsed response body
  otherwise. Base URL defaults to the US platform (`api.boomi.com`) —
  confirm the trial account is actually on that region before relying on it
  (`BOOMI_BASE_URL` overrides it). Credentials default to env vars but can
  be passed as kwargs.
- `scripts/package_component.py`, `scripts/deploy_component.py`,
  `scripts/run_tests.py` — CLI entry points the workflows call.
- `scripts/list_processes.py` — lists live Boomi components by type; for
  `process` (the default) also shows each one's latest packageId. Purely
  informational now — ci.yml/cd.yml resolve these themselves — but useful
  for a quick look, or when a name is ambiguous and an explicit
  componentId is needed.
- `scripts/list_packages.py` — every package (not just latest) for one
  process, for picking an older packageId as a rollback target.
- `scripts/write_ad_hoc_config.py` — writes a one-process components.json-
  shaped config for an ad-hoc `ci.yml` run; resolves `component_id` from
  `process_name` via `find_component_by_name()` if not given explicitly.
  Prints `resolved-component-id=<id>` on stdout (diagnostics go to stderr)
  so a workflow step can capture it straight into `$GITHUB_OUTPUT`.
- `scripts/update_deployment_ledger.py` — appends to `deployments/ledger.csv`
  and upserts `deployments/current.csv` after a successful deploy. Pure
  file I/O (no Boomi/GitHub calls) — the calling workflow step does the
  `git commit`/`push`. See `deployments/README.md`.
- `scripts/resolve_current_package.py` — for `cd.yml`: resolves
  `process_name` + `--environment` to the packageId `deployments/current.csv`
  says is live there right now, printing `package-id=<id>` and
  `component-id=<id>`. Falls back to Boomi's "latest ever packaged" lookup
  (the old `resolve_latest_package.py`, since removed — nothing else called
  it) if there's no ledger row yet.
- `scripts/resolve_rollback_package.py` — for `rollback.yml`: resolves
  `process_name` + `--environment` to the packageId that was actually live
  in that environment just before the current one (`--skip N` for further
  back), read from `deployments/ledger.csv` with real timestamps. Falls
  back to global package-creation order via the Boomi API (ignoring
  environment) if the ledger doesn't have enough per-environment rows yet.
  Always writes a job-summary table either way.
- `.github/workflows/ci.yml` ("Build & Deploy to Dev") — `workflow_dispatch`
  only (dropped push/pull_request triggers — packaging/deploying to Dev is
  deliberately a manual action now, not something that fires on every
  commit; see "What's still open" #5). `process_name` (or a `process_name` +
  explicit `component_id`) runs one ad-hoc process; leaving both blank
  packages/deploys everything in `components/components.json` instead.
  Opens/closes a tracking GitHub Issue on every run, posts a native GitHub
  Deployment record (`dev`), records the deploy into
  `deployments/{ledger,current}.csv` and pushes that commit back, and has
  a `concurrency` group keyed on `process_name` (push/PR runs share one
  bucket since they all target `components.json`).
- `.github/workflows/cd.yml` — `workflow_dispatch` with `process_name`,
  optional `package_id` (blank = whatever the ledger says is currently
  deployed one environment down — Dev's for a `qa` promotion, QA's for
  `prod`), and `target_environment` (qa|prod); deploys that package to the
  chosen environment (`prod` maps to the `production` GitHub Environment
  for its reviewer gate), records it into the ledger, and opens/closes a
  tracking Issue. Split into a `prepare` job (resolves the package, opens
  the issue) and a `deploy` job (the environment-gated one, posts a
  GitHub Deployment record, records the ledger, closes the issue) so a
  reviewer approving a prod promotion sees the linked issue, not just raw
  inputs — see "What's still open" #2 below, now closed. Also declares
  `workflow_call` inputs so `rollback.yml` can invoke it directly, reusing
  the same issue/approval/deployment-record/ledger path for a rollback.
  Has a `concurrency` group keyed on process+environment.
- `.github/workflows/rollback.yml` — `workflow_dispatch` with
  `process_name`, `target_environment` (qa|prod), optional `package_id`
  (auto-resolves via `scripts/resolve_rollback_package.py` to the package
  actually live in that environment just before the current one, from the
  ledger, if left blank). Delegates the actual deploy to `cd.yml`.
- `.github/workflows/list-processes.yml` / `list-packages.yml` — one-off
  `workflow_dispatch` lookups (components+latest-package; full package
  history for one process), printed to the Actions job summary.
- `.github/workflows/debug-secrets.yml` — prints SHA256 hashes of the Boomi
  secrets (never the values), to sanity-check what got saved.

## What's still open

1. **Flesh out `scripts/run_tests.py`.** It currently assumes each test case
   in `tests/*.json` triggers the process via a simple HTTP POST to a listener
   URL and polls the Execution Record API for `COMPLETE`. Confirm this
   matches how the actual pilot process is triggered (it may be a different
   listener type) once that process is chosen, and fill in the real
   assertion logic (`_check_expectation()` is a stub).
2. ~~The tracking issue is created inside the gated job~~ — fixed: `cd.yml`
   now splits into `prepare` (resolves the package, opens the issue —
   ungated) and `deploy` (the environment-gated job, posts a Deployment
   record, closes the issue). A reviewer approving a prod promotion now
   sees the linked issue already open. Not yet confirmed against a live
   run: whether GitHub's required-reviewer prompt actually appears for
   `deploy` when the run originates from `rollback.yml`'s `workflow_call`
   into `cd.yml` rather than a direct `workflow_dispatch` — reusable
   workflows are documented to respect environment protection rules, but
   this hasn't been exercised against this repo's actual `production`
   Environment yet.
3. **Name resolution assumes unique process names.** `find_component_by_name()`
   raises (failing the run) if two live components share a name and type —
   the fix in that case is passing an explicit `component_id`/`package_id`
   (found via `list-processes.yml`/`list-packages.yml`), not silently
   guessing which one was meant.
4. **`ci.yml`/`cd.yml` now push commits back to the repo** (the deployment
   ledger update in `deployments/`), using `permissions: contents: write`
   and the run's own `GITHUB_TOKEN` — deliberately not a PAT, so GitHub's
   built-in "don't re-trigger push-triggered workflows for GITHUB_TOKEN
   commits" safeguard applies and `ci.yml` (push-triggered) can't loop on
   itself. Not yet exercised against a real PR from a fork: GitHub forces
   a read-only `GITHUB_TOKEN` for `pull_request` runs from forks regardless
   of the `permissions:` block, so the ledger push would fail there — it's
   handled gracefully (a `::warning::` annotation, not a failed run), just
   confirm that's still the desired behavior once real PRs are in the mix.
5. **`ci.yml` dropped its `push`/`pull_request` triggers** (workflow_dispatch
   only now). Every automatic run was failing anyway — `components.json`'s
   pilot process_id is still the placeholder from context item #1 above —
   and packaging/deploying to Dev on every commit wasn't wanted even once
   that's fixed; it's meant to be a deliberate developer action. If a
   push-triggered path is wanted again later, it needs to be re-added
   on purpose, not assumed.

## Ground rules

- Never commit secrets or tokens.
- Every script should run locally with env vars for testing
  (`BOOMI_ACCOUNT_ID`, `BOOMI_USERNAME`, `BOOMI_API_TOKEN`) before it's
  wired into a workflow — don't make the GitHub Actions run the only way to
  test a change.
- Keep `dev`/`qa`/`prod` handling symmetric in the scripts — no special-casing
  one environment's code path, only its config values.
- `ci.yml`/`cd.yml`'s tracking-issue steps use plain `curl`/`jq` against
  `secrets.GITHUB_TOKEN` (both preinstalled on `ubuntu-latest`), matching the
  pattern in `qco-incorta-cicd`'s workflows — don't introduce
  `actions/github-script` or an extra Action for this without a reason to.
