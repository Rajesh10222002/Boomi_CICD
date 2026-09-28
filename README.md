# Boomi CI/CD Pilot

Automates packaging, testing, and promoting Boomi integration processes through
**Dev → QA → PD**, driven by GitHub Actions, with a free static GitHub Pages
admin console for triggering it.

Full design doc (architecture, decisions, open questions): see the linked plan
shared with this repo, or `docs/PLAN.md` once exported here.

## Layout

```
components/    Boomi process/component references to package (components.json)
environments/  Per-environment config: environment IDs, atom IDs, extension values
tests/         Test-case definitions (input + expected outcome) per process
scripts/       Python helpers that call the Boomi AtomSphere Platform API
.github/workflows/
  ci.yml               Package + deploy-to-Dev + run tests, on every push to main
  cd.yml               Promote a tested package to QA or PD (workflow_dispatch)
  publish-processes.yml  Snapshots live Boomi processes to docs/processes.json
  list-processes.yml    One-off Boomi process lookup, printed to a job summary
  debug-secrets.yml    Prints SHA256 hashes of the Boomi secrets (never values)
docs/          GitHub Pages admin console (static HTML/JS, no server, no
               third-party hosting) — process picker + promote buttons
```

## Why no Streamlit / no separate hosting

An earlier version of this used Streamlit Community Cloud for the admin UI.
That means a third party (Snowflake/Streamlit) would hold the Boomi and
GitHub tokens and every API call would run on their infrastructure — a real
compliance concern for handling this org's credentials and deployment data.
`docs/index.html` replaces it: it's a static page hosted for free by GitHub
Pages, in the same repo, under the same GitHub access control as everything
else here. Nothing in it ever talks to Boomi directly — a workflow
(`publish-processes.yml`) does that server-side, using the same repo secrets
as `ci.yml`/`cd.yml`, and just writes a JSON snapshot the page reads.

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

   Once these are set, run the **"Publish Boomi Processes"** workflow once
   from the Actions tab so `docs/processes.json` has real data (it also runs
   every 15 minutes on its own after that). Use the **"Debug Boomi Secrets"**
   workflow to sanity-check what got saved without ever printing the values.
3. **Enable GitHub Pages** — Settings → Pages → Source: "Deploy from a
   branch" → branch `main`, folder `/docs`. The admin console will then be at
   `https://<your-username>.github.io/Boomi_CICD/`.
4. **GitHub Environments** — Settings → Environments → create `qa` and
   `production`. On `production`, add required reviewers as the human
   approval gate on PD promotions.
5. **Who can use the admin console** — access is controlled entirely by
   GitHub, not by anything in this repo: only people you add as repo
   collaborators (or org members with access) can generate a personal access
   token that will actually work against this repo, and the `production`
   Environment's required reviewers (step 4) further restrict who can
   approve a PD promotion. Each admin generates their **own** fine-grained
   PAT (Settings → Developer settings → Personal access tokens → Fine-grained
   tokens), scoped to just this repo with "Actions: Read and write", and
   pastes it into the admin console once — it's kept only in that browser's
   local storage, never committed, never sent anywhere but `api.github.com`.

## What's scaffolded vs. what's still TODO

The admin console and the `component_id`/`package_id` it passes through are
live — no placeholder IDs there. Still to do:
- Fill in the real atom IDs in `environments/*.json` once an atom/runtime is
  attached to each Boomi environment (environment IDs are already filled in).
- `components/components.json` still has a placeholder `component_id` — it
  only matters for the push-triggered `ci.yml` pipeline (packages/tests
  whatever's listed there on every push to `main`); the admin-console flow
  doesn't touch this file at all.
- Write the real expected-output assertions in `tests/*.json` once the pilot
  process is chosen.

See `CLAUDE.md` for what to hand to Claude Code next.
