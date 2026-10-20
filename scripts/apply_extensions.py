"""
Set a process's connection extensions in a Boomi environment from the extension
value store (see extension_store.py) — or just plan / dry-run that.

For the process and every process it calls (any depth), find the connections used,
look up that environment's stored values per connection, and update ONLY the
extension fields that have a stored value. Everything else keeps what was deployed
(i.e. the Dev values). Dev is never touched.

Modes (--mode / EXT_MODE):
    plan      Before approval. Reads the store METADATA only (no values) and the field
              list from the Dev environment (where the process already lives). Changes
              nothing. Shows which connections will take stored values and which
              secrets are missing.
    dry-run   After deploy. Reads the target environment's extensions and the stored
              values (decrypting them, to prove they're usable), builds the update, but
              does not send it.
    apply     After deploy. Sends the partial update and re-reads Boomi to verify.

Missing-secret policy (--missing-policy / EXT_MISSING_POLICY): a connection with an
encrypted (password-type) extension field that has no stored value is a "missing
secret". 'stop' fails the run (exit 3) — the default for prod/PD; 'warn' reports it and
carries on — the default for QA. Missing fields never fall back to guesses.

Never prints a value. Output goes to stdout as key=value lines for $GITHUB_OUTPUT
(also written there directly when that env var is set), a markdown report to
$GITHUB_STEP_SUMMARY / --out, and optionally a comment on a GitHub issue.

Usage:
    python scripts/apply_extensions.py --process-name "My Process" --environment qa --mode plan
    EXT_STORE=file EXT_STORE_FILE=values.json SECRETS_KEY=... \
        python scripts/apply_extensions.py --process-name "My Process" --environment qa --mode dry-run
"""

import argparse
import copy
import os
import sys

from boomi_client import BoomiApiError, BoomiClient
from extension_probe import _as_list, _sections, collect_dependencies, environment_id
from extension_store import StoreError, open_store, rows_for_connection


def _is_encrypted(field):
    return bool(field.get("usesEncryption") or field.get("encryptedValueSet") or field.get("type") == "password")


def _field_id(field):
    return field.get("id") or field.get("label") or ""


def _connection_items(extensions):
    return {item.get("id"): item for section, item in _sections(extensions) if section == "connections"}


def analyse(connections, processes, field_source, rows):
    """Per connection: what would be set, what's missing. Values (if present in rows) are carried but never printed."""
    items = _connection_items(field_source)
    results = []
    for conn_id, users in connections.items():
        item = items.get(conn_id)
        res = {"id": conn_id, "name": (item or {}).get("name") or conn_id, "used_by": users,
               "listed": item is not None, "set": {}, "unknown": [], "missing_secret": [], "note": ""}
        fields = {}
        if item is not None:
            for f in _as_list(item.get("field")):
                if isinstance(f, dict) and _field_id(f):
                    fields[_field_id(f).lower()] = f
        mine = rows_for_connection(rows, conn_id, res["name"])
        if item is None:
            res["note"] = ("stored values exist but the connection isn't listed in the extensions yet" if mine
                           else "not listed as extensible")
            res["unknown"] = [r["field_name"] for r in mine]
        else:
            for r in mine:
                f = fields.get(r["field_name"].lower())
                if f is None:
                    res["unknown"].append(r["field_name"])
                else:
                    res["set"][_field_id(f)] = r
            res["missing_secret"] = [_field_id(f) for f in fields.values()
                                     if _is_encrypted(f) and _field_id(f) not in res["set"]]
            if not mine:
                res["note"] = "no stored values — keeps the deployed (Dev) values"
        results.append(res)
    return results


def build_update(extensions, results):
    """Partial EnvironmentExtensions payload containing only the fields to change."""
    changes = {r["id"]: r["set"] for r in results if r["set"]}
    payload = {k: v for k, v in extensions.items() if k.startswith("@") or k in ("environmentId", "extensionGroupId")}
    payload["partial"] = True
    conns = extensions.get("connections") or {}
    out_items = []
    for item in _as_list(conns.get("connection")):
        if not isinstance(item, dict) or item.get("id") not in changes:
            continue
        new_item = {k: v for k, v in item.items() if k != "field"}
        new_item["field"] = []
        for f in _as_list(item.get("field")):
            fid = _field_id(f)
            if fid in changes[item["id"]]:
                nf = copy.deepcopy(f)
                nf["value"] = changes[item["id"]][fid]["value"]
                nf["useDefault"] = False
                if "encryptedValueSet" in nf or _is_encrypted(nf):
                    nf["encryptedValueSet"] = True
                new_item["field"].append(nf)
        out_items.append(new_item)
    new_conns = {k: v for k, v in conns.items() if k != "connection"}
    new_conns["connection"] = out_items
    payload["connections"] = new_conns
    return payload


def verify(client, env_id, results):
    """Re-read Boomi and check each changed field is no longer on its default. Returns problems (names only)."""
    items = _connection_items(client.get_environment_extensions(env_id))
    problems = []
    for r in results:
        item = items.get(r["id"])
        if not r["set"]:
            continue
        if item is None:
            problems.append(f"{r['name']}: connection missing after update")
            continue
        fields = {_field_id(f): f for f in _as_list(item.get("field")) if isinstance(f, dict)}
        for fid, row in r["set"].items():
            f = fields.get(fid)
            if f is None or f.get("useDefault") is not False:
                problems.append(f"{r['name']}.{fid}: still using the default")
            elif not _is_encrypted(f) and f.get("value") != row["value"]:
                problems.append(f"{r['name']}.{fid}: value differs after update")
    return problems


def _scrub(text, results):
    for r in results:
        for row in r["set"].values():
            v = row.get("value")
            if v:
                text = text.replace(v, "***")
    return text[:400]


def render(process_name, environment, mode, results, policy, stopped):
    icon = {"plan": "🗺️", "dry-run": "🧪", "apply": "🔐"}[mode]
    lines = [f"## {icon} Extensions — `{process_name}` → **{environment}** ({mode})", ""]
    if mode == "plan":
        lines += ["_Plan only: field list read from Dev, store read as metadata. Nothing is changed and no value is read._", ""]
    lines += [f"Missing-secret policy for {environment}: **{policy}**", "",
              "| Connection | Will set from store | Missing secrets | Store fields not in Boomi | Note |",
              "|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r['name']} | {', '.join(f'`{f}`' for f in r['set']) or '—'} | "
            f"{', '.join(f'`{f}`' for f in r['missing_secret']) or '—'} | "
            f"{', '.join(f'`{f}`' for f in r['unknown']) or '—'} | {r['note'] or ''} |"
        )
    if not results:
        lines.append("| _(no connections found in the process tree)_ | | | | |")
    lines.append("")
    if stopped:
        lines.append("> ❌ **Stopping:** missing secrets for this environment — add them to the store before promoting.")
    elif any(r["missing_secret"] for r in results):
        lines.append("> ⚠️ Missing secrets are left on their deployed (Dev) values.")
    return "\n".join(lines) + "\n"


def _write_env_file(var, text):
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def _post_issue_comment(body):
    import requests

    number, repo, token = os.environ.get("ISSUE_NUMBER"), os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (number and repo and token):
        return
    requests.post(
        f"https://api.github.com/repos/{repo}/issues/{number}/comments",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"body": body}, timeout=30,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", default=os.environ.get("PROCESS_NAME", ""))
    parser.add_argument("--component-id", default="")
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT", ""), choices=["qa", "prod", ""])
    parser.add_argument("--mode", default=os.environ.get("EXT_MODE", "plan"), choices=["plan", "dry-run", "apply"])
    parser.add_argument("--missing-policy", default=os.environ.get("EXT_MISSING_POLICY", ""), choices=["warn", "stop", ""])
    parser.add_argument("--out", default="extensions_report.md")
    args = parser.parse_args()
    if not args.process_name or not args.environment:
        parser.error("--process-name and --environment are required (or PROCESS_NAME / ENVIRONMENT)")
    policy = args.missing_policy or ("stop" if args.environment == "prod" else "warn")

    try:
        client = BoomiClient()
        component_id = args.component_id or client.find_component_by_name(args.process_name)
        connections, processes = collect_dependencies(client, component_id)
        if args.mode == "plan":
            field_source = client.get_environment_extensions(environment_id("dev"))
            rows = open_store("plan").rows(args.environment, with_values=False)
        else:
            field_source = client.get_environment_extensions(environment_id(args.environment))
            rows = open_store("apply").rows(args.environment, with_values=True)
    except (StoreError, BoomiApiError, LookupError) as exc:
        print(f"::error::Extension step failed before any change: {exc}", file=sys.stderr)
        sys.exit(2)

    results = analyse(connections, processes, field_source, rows)
    missing = sum(len(r["missing_secret"]) for r in results)
    to_set = sum(len(r["set"]) for r in results)
    stopped = bool(missing) and policy == "stop"

    problems = []
    if args.mode == "apply" and to_set and not stopped:
        payload = build_update(field_source, results)
        try:
            client.update_environment_extensions(environment_id(args.environment), payload)
        except BoomiApiError as exc:
            print(f"::error::Boomi rejected the extension update: {_scrub(str(exc), results)}", file=sys.stderr)
            sys.exit(2)
        problems = verify(client, environment_id(args.environment), results)

    report = render(args.process_name, args.environment, args.mode, results, policy, stopped)
    if args.mode == "apply" and not stopped:
        report += (f"\n✅ Applied {to_set} field(s) and verified in Boomi.\n" if not problems and to_set
                   else (f"\n❌ Verification problems: {'; '.join(problems)}\n" if problems else "\nNothing to apply.\n"))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    _write_env_file("GITHUB_STEP_SUMMARY", report + "\n")
    _post_issue_comment(report)
    _write_env_file("GITHUB_OUTPUT", f"extensions-to-set={to_set}\nextensions-missing-secrets={missing}\n")
    print(f"extensions-to-set={to_set}")
    print(f"extensions-missing-secrets={missing}")
    print(report, file=sys.stderr)

    if stopped:
        sys.exit(3)
    if problems:
        sys.exit(4)


if __name__ == "__main__":
    main()
