# Boomi CI/CD Pilot

Automates packaging, testing, and promoting Boomi integration processes through
**Dev → QA → PD**, driven entirely by GitHub Actions — no separate admin app,
no third-party hosting, no custom login. You trigger runs from the Actions
tab, and every manual run opens (then closes) a GitHub Issue as its audit
record.

Full design doc (architecture, decisions, open questions): see the linked plan
shared with this repo, or `docs/PLAN.md` once exported here.

## Layout

```
components/    Boomi process/component references to package (components.json)
environments/  Per-environment config: environment IDs, atom IDs, extension values
deployments/   ledger.csv + current.csv — this repo's own record of what's
                actually deployed where, and its history. See deployments/README.md.
tests/         Test-case definitions (input + expected outcome) per process
scripts/       Python helpers that call the Boomi AtomSphere Platform API
.github/workflows/
  ci.yml             Package + deploy-to-Dev + run tests (workflow_dispatch
                      only — a deliberate developer action, not on every
                      push). Requires a version label (e.g. v1.1) — becomes
                      the Boomi package's version and is recorded in the
                      ledger. Records the Dev deploy into deployments/.
  cd.yml             Promote to QA or PD (workflow_dispatch) — always takes
                      whatever the ledger says is currently deployed one
                      environment down (Dev's current package for a qa
                      promotion, QA's for a prod promotion); there is no
                      package_id input on this form, so a process that was
                      never actually deployed to the environment below
                      cannot be promoted — the run fails with a clear
                      message instead of guessing. Also callable by
                      rollback.yml (workflow_call, the one place an explicit
                      older package_id is accepted) so a rollback gets the
                      same tracking issue / approval gate / deployment
                      record / ledger update as a normal promotion.
  rollback.yml       Redeploy an older package to QA or PD (workflow_dispatch)
                      — leave package_id blank to auto-pick the version that
                      was actually live in that environment before the
                      current one, from the ledger (fails if there isn't
                      enough per-environment history yet, rather than
                      guessing from Boomi's global package list).
  list-processes.yml  Look up live Boomi processes + their latest package,
                      printed to a job summary. Purely informational —
                      ci.yml/cd.yml resolve process names themselves now.
  list-packages.yml  Every package (not just latest) for one process, for
                      picking an older packageId as a rollback target.
  debug-secrets.yml  Prints SHA256 hashes of the Boomi secrets (never values).
```

## Why no custom admin app

Two earlier approaches were tried and dropped:
- **Streamlit Community Cloud** — would put the Boomi and GitHub tokens, and
  every API call, on third-party (Snowflake/Streamlit) infrastructure. A real
  compliance concern for this org's credentials and deployment data.
- **A static GitHub Pages page with a pasted personal access token** — free
  and stays inside GitHub, but the token sits in each admin's browser storage
  with no central visibility, expiry enforcement, or revocation.

Instead, this repo uses GitHub's own `workflow_dispatch` forms (Actions tab →
pick a workflow → "Run workflow") as the trigger, and each manual run opens a
GitHub Issue with the request's details (who, what, when, why) and closes it
with the outcome when done — matching how Quanta's other Boomi/Incorta CI/CD
repos (`qco-incorta-cicd`, `qco-incorta-ui`) already do this in production.
Access control is entirely GitHub's: only repo collaborators can dispatch a
workflow at all, and the `production` Environment's required reviewers (see
setup step 4) gate PD promotions specifically. No login page, no token to
paste anywhere, nothing to revoke beyond normal GitHub repo access.

(A Databricks App front end was also tried, since Databricks is a vendor
this org already has a relationship with — but decided against for now, to
keep everything in one place with nothing extra to deploy/maintain. The
native `workflow_dispatch` flow below is the only supported path.)

## One-time setup (you do this, not Claude Code)

1. **Boomi API token** — Boomi trial account → Settings → My User Settings →
   Platform API Tokens → Add New Token. Copy the value immediately.
2. **GitHub repo secrets** — Settings → Secrets and variables → Actions → New
   repository secret. Add:
   - `BOOMI_ACCOUNT_ID`
   - `BOOMI_USERNAME` — format `BOOMI_TOKEN.<your-boomi-login-email>`
   - `BOOMI_API_TOKEN` — the token value from step 1
   - `BOOMI_BASE_URL` — only if the trial account is NOT on the US platform
     (defaults to `https://api.boomi.com`; use `https://api.platform.gb.boomi.com`
     for EU/GB accounts)

   Use the **"Debug Boomi Secrets"** workflow afterward to sanity-check what
   got saved, without it ever printing the actual values.
3. **GitHub Environments** — Settings → Environments → create `qa` and
   `production`. On `production`, add required reviewers as the human
   approval gate on PD promotions.
4. **Who can trigger runs** — controlled entirely by GitHub repo access, not
   by anything in this repo: add exactly the people who should be able to
   dispatch `ci.yml`/`cd.yml` as collaborators (Settings → Collaborators and
   teams), and rely on step 3's required reviewers for the smaller set who
   can approve a PD promotion specifically.

## Using it

Only a process name is needed for either workflow — `componentId`/`packageId`
resolve automatically server-side (via `BoomiClient.find_component_by_name()`
/ `find_latest_package()`), the same way "List Boomi Processes" looks them up.

1. **Build & deploy to Dev**: Actions tab → **`ci.yml`** → Run workflow →
   fill in `process_name` (leave `component_id` blank) and `version` (e.g.
   `v1.1` — required; this is the version being updated in the Boomi
   process, becomes the packaged component's version in Boomi, and is
   recorded in the ledger). Opens a tracking Issue, closes it with the
   outcome when the run finishes.
2. **Promote to QA/PD**: Actions tab → **`cd.yml`** → Run workflow →
   `process_name`, `target_environment` (`qa` or `prod`). There's no
   `package_id` field here — promotion always takes whatever the
   deployment ledger says is *currently deployed one environment down*
   (Dev's current package for a `qa` promotion, QA's for a `prod`
   promotion), a real Dev → QA → PD chain. A process that hasn't actually
   been deployed to the environment below yet can't be promoted — the run
   fails with a clear message rather than guessing. Same tracking-Issue
   pattern; promoting to `prod` additionally waits on the `production`
   Environment's reviewers.
3. **Rollback**: Actions tab → **`rollback.yml`** → Run workflow →
   `process_name`, `target_environment`. Leave `package_id` blank and it
   auto-picks the package that was actually live in that environment just
   before the current one, read from the deployment ledger with real dates
   (job summary always prints the table, so you can confirm the choice);
   fails if there isn't at least two distinct successful deploys of this
   process to this environment recorded yet, rather than guessing — pass an
   explicit `package_id` instead (one that's genuinely been live in this
   environment; check `deployments/ledger.csv` or the job summary from a
   previous promotion). Goes through the same tracking issue, deployment
   record, ledger update, and (for prod) required-reviewer approval as a
   normal promotion.
4. **"List Boomi Processes"** and **component_id** input on `ci.yml`
   still exist as an explicit override / manual lookup if you ever need
   them (e.g. a component name isn't unique, or you want to double-check
   what resolved), but day-to-day you shouldn't need them.
5. **Deployment history**: every `ci.yml`/`cd.yml`/`rollback.yml` run also
   posts a native GitHub Deployment record (repo → **Environments** tab),
   so `dev`/`qa`/`production` each show their own history — who, which
   commit, success/failure — alongside the tracking Issues. On top of that,
   a successful deploy is recorded into `deployments/ledger.csv` (full
   history) and `deployments/current.csv` (what's live right now, per
   process+environment) — see `deployments/README.md`. That's what makes
   the Dev → QA → PD chaining in #2 and the accurate rollback in #3 possible.
   Two runs targeting the same process+environment can't overlap: each
   workflow sets a `concurrency` group, so a second dispatch queues behind
   the first instead of racing it.

## What's scaffolded vs. what's still TODO

- Fill in the real atom IDs in `environments/*.json` once an atom/runtime is
  attached to each Boomi environment (environment IDs are already filled in).
- `components/components.json` still has a placeholder `component_id` — it
  only matters for a `ci.yml` run with `process_name`/`component_id` left
  blank (packages/deploys whatever's listed there); a `workflow_dispatch`
  run with an explicit `process_name` or `component_id` bypasses this file
  entirely.
- Write the real expected-output assertions in `tests/*.json` once the pilot
  process is chosen. Until then, `run_tests.py` skips (doesn't fail the run
  on) any test case whose `listener_url` is still the placeholder — so
  `ci.yml` can still succeed and record a deploy while this is unfinished.

See `CLAUDE.md` for what to hand to Claude Code next.
