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
                      printed to a job summary — use this to fill in the
                      componentId/packageId that ci.yml/cd.yml ask for.
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

1. Run **"List Boomi Processes"** (Actions tab → Run workflow) to see live
   process names, componentIds, and each one's latest packageId in the job
   summary.
2. Run **`ci.yml`** (workflow_dispatch) with a `process_name` + `component_id`
   from that list to build/deploy/test one process against Dev. This opens a
   tracking Issue, closes it with the outcome when the run finishes.
3. Run **`cd.yml`** with the `process_name`, the `package_id` from step 1 (or
   from `ci.yml`'s own packages.json artifact), and `target_environment`
   (`qa` or `prod`) to promote it. Same tracking-Issue pattern; promoting to
   `prod` additionally waits on the `production` Environment's reviewers.

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
