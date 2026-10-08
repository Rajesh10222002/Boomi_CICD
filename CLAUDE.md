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
- Whether PD promotion needs a required-reviewer approval in GitHub — now
  moot for the *mechanism* (every workflow references a `dev`/`qa`/
  `production` GitHub Environment, so any of them can gate on reviewers),
  but which environments actually have reviewers configured, and who, is
  still a human Settings step (the owner is starting by adding themselves
  as the reviewer everywhere, to test the gate end to end).

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
- `scripts/write_ad_hoc_config.py` — writes a one-process components.json-
  shaped config for an ad-hoc `ci.yml` run; resolves `component_id` from
  `process_name` via `find_component_by_name()` if not given explicitly.
  Prints `resolved-component-id=<id>` on stdout (diagnostics go to stderr)
  so a workflow step can capture it straight into `$GITHUB_OUTPUT`.
- `scripts/update_deployment_ledger.py` — appends to `deployments/ledger.csv`
  and upserts `deployments/current.csv` after a successful deploy. Both now
  have a `version` column (the label `ci.yml`'s `version` input assigned at
  build time). `--component-id`/`--version` are optional: if omitted, looks
  up the most recent ledger row for the same `--package-id` and inherits
  its values. Pure file I/O (no Boomi/GitHub calls) — the calling workflow
  step does the `git commit`/`push`. See `deployments/README.md`.
- `scripts/resolve_current_package.py` — for `cd.yml`: resolves
  `process_name` + `--environment` to the packageId/componentId/version
  `deployments/current.csv` says is live there right now. No fallback to Boomi's "latest ever packaged" lookup (the old
  `resolve_latest_package.py`, since removed) — if there's no ledger row, it falls back to Boomi's *actual* active
  deployment in that environment (`live_deployment.py` /
  `BoomiClient.find_deployed_package()`), so a process deployed by hand in
  the Boomi UI can still be promoted; it hard-fails only if neither has it. That's the enforcement mechanism for "a process can only
  be promoted from an environment it's actually been deployed to."
- `scripts/describe_current.py` — display-only (never used for a
  control-flow decision, unlike `resolve_current_package.py`): prints a
  one-line human-readable description of whatever `deployments/current.csv`
  says is live for a process+environment right now, or
  "(nothing deployed yet)". Used by `ci.yml`/`cd.yml`'s `prepare` step to
  build the before -> after diff shown in the tracking issue and job
  summary, ahead of the environment-protection approval gate. Also prints
  `current-package-id`, the raw id `diff_component_versions.py` uses as
  its "before" package.
- `scripts/diff_component_versions.py` — a structural diff (elements
  added/removed, attribute "X changed from A to B") between two packages'
  underlying component definitions, via the new
  `BoomiClient.get_component_xml()` — not a raw XML/line diff, which is
  unreadable anyway since Boomi returns the XML minified onto one line.
  Parses both documents with `xml.etree.ElementTree` and walks them in
  parallel, matching child elements by tag + an identifying attribute
  (`name`/`id`/`key`) where one exists, positionally within same-tag
  groups otherwise; falls back to a raw pretty-printed line diff only if
  a document fails to parse as XML at all. Confirmed against a real
  account (run 36540110917's later promotions): PackagedComponent does
  expose `componentVersion`, and Component GET does accept
  `{componentId}~{version}` for a historical revision — kept failing
  soft anyway (`xml-diff-available=false` + a reason to stderr, no
  exception) in case either behaves differently on some other account, so
  `ci.yml`/`cd.yml` wire it in with `continue-on-error: true`.
- `.github/workflows/ci.yml` ("Build & Deploy to Dev") — `workflow_dispatch`
  only (dropped push/pull_request triggers — packaging/deploying to Dev is
  deliberately a manual action now, not something that fires on every
  commit; see "What's still open" #5). **`process_name` and `version`
  (e.g. `v1.1`) are both required** — no more "leave both process_name and
  component_id blank to bulk-package components.json" path; this workflow
  no longer reads `components/components.json` at all. `component_id`
  stays optional, only for disambiguating a non-unique name. `version`
  becomes the Boomi package's `packageVersion` and is recorded in the
  ledger. Split into a `prepare` job (resolves the process, opens a
  tracking Issue with a `dev: <before> -> <after>` diff via
  `describe_current.py`, prints the same to the job summary) and a
  `build-and-deploy` job (`environment: dev` — gated on that Environment's
  required reviewers if any are configured; packages, deploys, posts a
  native GitHub Deployment record, records the ledger, runs tests, closes
  the issue), mirroring `cd.yml`'s split so the reviewer isn't approving
  blind. Has a `concurrency` group keyed on `process_name`.
- `.github/workflows/cd.yml` — `workflow_dispatch` with only
  `process_name`. It resolves the current Dev package from the ledger,
  deploys it to QA, then automatically deploys that same package to
  production only after QA succeeds. Prepare jobs open tracking issues with
  before/after diffs ahead of the environment gates; both deployments update
  the GitHub Deployment record and ledger. Rollback/reusable-call,
  package-history, and debug-secrets workflows have been removed.
- `.github/workflows/list-processes.yml` — one-off `workflow_dispatch`
  lookup for live Boomi processes and their latest package, printed to the
  job summary.

## What's still open

1. **Flesh out `scripts/run_tests.py`.** It currently assumes each test case
   in `tests/*.json` triggers the process via a simple HTTP POST to a listener
   URL and polls the Execution Record API for `COMPLETE`. Confirm this
   matches how the actual pilot process is triggered (it may be a different
   listener type) once that process is chosen, and fill in the real
   assertion logic (`_check_expectation()` is a stub). Until then, a test
   case whose `listener_url` is still the placeholder is **skipped** (not a
   failure) so `ci.yml` can still succeed — confirmed against a real run
   (`Boomi Basics Demo`, run 36540110917) where packaging/deploy/ledger all
   succeeded and only the still-templated `tests/sample_test_case.json`
   was skipped.
2. ~~The tracking issue is created inside the gated job~~ — fixed: `cd.yml`
   splits into `prepare` (resolves the package, opens the issue — ungated)
   and `deploy` (the environment-gated job, posts a Deployment record,
   closes the issue). A reviewer sees the linked issue before approving.
3. **Name resolution assumes unique process names.** `find_component_by_name()`
   raises (failing the run) if two live components share a name and type —
   the fix in that case is passing an explicit `component_id` (found via
   `list-processes.yml`), not silently
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
6. **The "must be deployed one environment down first" gate and the
   `version` input are new and only exercised against real Boomi data for
   the `ci.yml` -> Dev leg so far** (see item #1's note on run 36540110917).
   Still to be confirmed against the real account: `cd.yml` promoting Dev's
   current package to `qa`, then `qa`'s to `prod`.
7. **Every workflow now references a `dev`/`qa`/`production` GitHub
   Environment for its approval gate** (`ci.yml` newly split into
   `prepare`/`build-and-deploy` for this — see its entry above), and every
   `prepare`-style job writes a before -> after diff to the tracking issue
   and job summary before that gate. Not yet exercised end-to-end against
   real required reviewers on all three Environments — the owner is
   starting by adding themselves as reviewer everywhere to test the gate
   (see the required-reviewer bullet under "Context you don't have yet").
   `dev` likely doesn't exist yet as a
   configured Environment (it auto-creates on first reference with no
   protection rules, i.e. ungated, until reviewers are added to it).
8. ~~`diff_component_versions.py`'s two Boomi-API assumptions are
   unconfirmed~~ — confirmed against a real account: `componentVersion`
   on PackagedComponent and `{id}~{version}` on Component GET both work.
   Went through two more rounds against real diff output:
   - The first version produced a raw XML line diff, useless because
     Boomi returns the whole document as one minified line ("one line
     changed to another line") — replaced with the structural
     element/attribute diff described above.
   - That still showed `object[0] -> process[0] -> shapes[0]` noise
     (meaningless indices on wrapper elements there's only ever one of)
     and gave zero context on added/removed elements (e.g. "shape
     'shape8': added" with no attributes) — a real run showed a shape
     removed + a same-typed shape added + a dragpoint's `toShape`
     retargeted, which reads exactly like Boomi internally renumbering
     an unchanged shape rather than a real content change, but there was
     no way to tell without seeing the shapes' own attributes. Fixed:
     indices are now only shown when a tag genuinely has multiple
     siblings, and added/removed elements print their full attribute set
     (`_attrs_str()`) so a same-type/same-position add+remove pair is
     visibly identifiable as a likely renumbering.
   Still not confirmed: whether dumping *all* attributes on add/remove
   turns out too noisy for a shape with many layout-only attributes
   (x/y-style canvas coordinates) once seen on a bigger real process —
   no evidence either way yet, so nothing's filtered out.

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
