# Instructions for Claude Code

This repo is a scaffold for a Boomi CI/CD pilot: GitHub Actions packages,
tests, and promotes a Boomi integration process through **Dev → QA → PD**.
There is no custom admin app — `workflow_dispatch` (Actions tab → "Run
workflow") is the trigger, and each manual run opens (then closes) a GitHub
Issue as its audit record. Two other approaches were tried and dropped; see
README.md's "Why no custom admin app" for why (Streamlit Community Cloud:
third-party credential custody; a static GitHub Pages page with a pasted
PAT: no central token visibility/revocation). This settled on matching how
Quanta's other Boomi/Incorta CI/CD repos (`qco-incorta-cicd`,
`qco-incorta-ui`) already do it in production.

Read `README.md` first for the layout and one-time setup the human owner is
handling separately (Boomi API token, repo secrets, GitHub Environments —
**don't ask for these as plaintext, don't put them in any file you commit**;
they arrive as GitHub Actions secrets, added directly in that UI).

## Context you don't have yet — ask before assuming

The human owner is still confirming these. If a task below needs one and it
isn't filled in yet, ask rather than inventing a value:
- Which Boomi process is the actual pilot for the **push-triggered** `ci.yml`
  pipeline (currently a placeholder id in `components/components.json`). Not
  needed for a manual `workflow_dispatch` run — `process_name` alone resolves
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
- `scripts/resolve_latest_package.py` — same idea for `cd.yml`: resolves
  `process_name` to its latest packageId, printing `package-id=<id>`.
- `.github/workflows/ci.yml` — on push to `main`: package → deploy to Dev →
  run tests, using `components/components.json`. `workflow_dispatch` with
  just `process_name` (or a `process_name` + explicit `component_id`) runs
  one ad-hoc process instead, and (only for `workflow_dispatch`)
  opens/closes a tracking GitHub Issue.
- `.github/workflows/cd.yml` — `workflow_dispatch` with `process_name`,
  optional `package_id` (resolves to the latest if blank), and
  `target_environment` (qa|prod); deploys that package to the chosen
  environment (`prod` maps to the `production` GitHub Environment for its
  reviewer gate) and opens/closes a tracking Issue.
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
2. **The tracking issue is created inside the gated job**, so for `prod`
   promotions it only gets created *after* the `production` Environment's
   required reviewers approve — an approver reviewing the pending deployment
   sees the workflow's raw inputs (GitHub shows those natively) but not yet
   a linked Issue. Splitting into a separate `wait_for_approval` job ahead of
   a `manual-approval` Environment (like `qco-incorta-cicd`'s `rollback.yml`
   does) would let the issue exist before approval; not done yet since the
   existing `production` Environment gate already covers the actual approval
   requirement.
3. **Name resolution assumes unique process names.** `find_component_by_name()`
   raises (failing the run) if two live components share a name and type —
   the fix in that case is passing an explicit `component_id`/`package_id`
   (found via `list-processes.yml`/`list-packages.yml`), not silently
   guessing which one was meant.

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
