# Boomi CI/CD Pilot

Automates packaging, testing, and promoting Boomi integration processes through
**Dev → QA → PD**, driven entirely by GitHub Actions — no separate admin app,
no third-party hosting, no custom login. You trigger runs from the Actions
tab. Deployment runs open (then close) a GitHub Issue as their audit record;
the CD workflow promotes the current Dev package to QA and, if QA succeeds,
automatically promotes that same package to production.

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
  cd.yml             Manually promote Dev -> QA, then
                      automatically promote the same package QA -> PD when
                      QA succeeds. Production Environment reviewers, if
                      configured, still gate the automatic promotion.
  list-processes.yml  Look up live Boomi processes + their latest package,
                      printed to a job summary. Purely informational —
                      ci.yml/cd.yml resolve process names themselves now.
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
workflow at all, and each `dev`/`qa`/`production` Environment's required
reviewers (see setup step 3) gate that environment's deploys specifically.
No login page, no token to paste anywhere, nothing to revoke beyond normal
GitHub repo access.

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

3. **GitHub Environments** — Settings → Environments → create `dev`, `qa`,
   and `production` (the deploy workflows reference these by name,
   so create all of them even if you don't add reviewers to all of them
   yet). On whichever ones should require approval before that job runs —
   `dev` gates `ci.yml`; `qa` and `production` gate the corresponding
   deployments in `cd.yml` — add yourself (or whoever
   should approve) as a required reviewer. Every deploy opens its tracking
   issue *before* this gate, with a before → after diff (version/packageId)
   in the issue body and that
   run's job summary, so the reviewer isn't approving blind — check those
   before clicking Approve in the "Review deployments" prompt GitHub shows
   on the run. The job summary also shows a **process diff** — actual
   structural changes (shape/element added or removed, an attribute
   changed from X to Y), not raw XML — uploaded as a `process-diff`
   artifact when it succeeds.
4. **Who can trigger runs** — controlled entirely by GitHub repo access, not
   by anything in this repo: add exactly the people who should be able to
   dispatch `ci.yml`/`cd.yml` as collaborators (Settings →
   Collaborators and teams), and rely on step 3's required reviewers for
   the smaller set who can approve a specific environment's deploys.

## Using it

A process name is all that's needed — `componentId` resolves automatically
server-side (via `BoomiClient.find_component_by_name()`, the same way "List
Boomi Processes" looks it up), and `packageId` resolves from this repo's own
deployment ledger, not a raw Boomi lookup.

1. **Build & deploy to Dev**: Actions tab → **`ci.yml`** → Run workflow →
   `process_name` and `version` (e.g. `v1.1`) are both required (leave
   `component_id` blank unless the name isn't unique). `version` becomes
   the packaged component's actual version in Boomi and is recorded in the
   ledger. Opens a tracking Issue with a `dev: <before> -> <after>` diff and
   prints the same to the run's job summary, then waits on the `dev`
   Environment's reviewers (if any are configured) before actually
   building/deploying.
2. **Promote Dev to QA, then automatically to PD**: Actions tab →
   **`cd.yml`** → Run workflow → enter `process_name`. The form has no
   checklist or version input. The workflow takes the package currently
   deployed in Dev, deploys it to QA, then automatically promotes that same
   package to production only after the QA deployment succeeds. There is no
   separate QA-to-production dispatch. QA and production reviewer gates
   remain in effect if configured; production approval can still pause the
   automatic follow-on job.

   Before the QA gate, `scripts/validate_checklist.py` pulls the package's
   component XML and runs the `checklist.md` items against it. The tracking
   issue and job summary list each item as PASS, FAIL, or REVIEW (can't be
   judged from XML — needs a human). Failures don't block the run; the QA
   reviewer reads the report and approves or rejects. Approving deploys to
   QA, then production follows (and hits its own gate). Most checks are
   heuristics over Boomi's process XML — tune `CONFIG` in the script.
3. **"List Boomi Processes"** and **component_id** input on `ci.yml`
   still exist as an explicit override / manual lookup if you ever need
   them (e.g. a component name isn't unique, or you want to double-check
   what resolved), but day-to-day you shouldn't need them.
4. **Deployment history**: every `ci.yml`/`cd.yml` run also
   posts a native GitHub Deployment record (repo → **Environments** tab),
   so `dev`/`qa`/`production` each show their own history — who, which
   commit, success/failure — alongside the tracking Issues. On top of that,
   a successful deploy is recorded into `deployments/ledger.csv` (full
   history) and `deployments/current.csv` (what's live right now, per
   process+environment) — see `deployments/README.md`. That's what makes
   the Dev → QA → PD chaining in #2 possible.
   Two `cd.yml` runs for the same process can't overlap: the workflow
   serializes the complete QA-to-production chain so a second dispatch
   queues behind the first instead of racing it.

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
