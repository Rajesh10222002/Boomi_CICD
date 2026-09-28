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
tests/         Test-case definitions (input + expected outcome) per process
scripts/       Python helpers that call the Boomi AtomSphere Platform API
.github/workflows/
  ci.yml             Package + deploy-to-Dev + run tests. Push to main, or
                      workflow_dispatch for one ad-hoc process.
  cd.yml             Promote a tested package to QA or PD (workflow_dispatch).
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
   just fill in `process_name` (leave `component_id` blank). Opens a
   tracking Issue, closes it with the outcome when the run finishes.
2. **Promote to QA/PD**: Actions tab → **`cd.yml`** → Run workflow →
   `process_name`, `target_environment` (`qa` or `prod`) — leave `package_id`
   blank to promote whatever was most recently built. Same tracking-Issue
   pattern; promoting to `prod` additionally waits on the `production`
   Environment's reviewers.
3. **Rollback**: there's no separate rollback button — run `cd.yml` again
   with an **explicit, older** `package_id` instead of leaving it blank. Use
   **"List Boomi Packages"** (Actions tab → Run workflow, with `process_name`)
   to see every past package for a process and pick one.
4. **"List Boomi Processes"** and **component_id**/**package_id** inputs
   still exist as an explicit override / manual lookup if you ever need
   them (e.g. a component name isn't unique, or you want to double-check
   what resolved), but day-to-day you shouldn't need either.

## What's scaffolded vs. what's still TODO

- Fill in the real atom IDs in `environments/*.json` once an atom/runtime is
  attached to each Boomi environment (environment IDs are already filled in).
- `components/components.json` still has a placeholder `component_id` — it
  only matters for the push-triggered `ci.yml` pipeline (packages/tests
  whatever's listed there on every push to `main`); a manual `workflow_dispatch`
  run with an explicit `component_id` bypasses this file entirely.
- Write the real expected-output assertions in `tests/*.json` once the pilot
  process is chosen.

See `CLAUDE.md` for what to hand to Claude Code next.
