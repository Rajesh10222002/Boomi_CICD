"""
Pre-promotion checklist validation (see checklist.md in the repo root).

Pulls the package's component XML from Boomi (same versioned fetch
diff_component_versions.py uses), runs each checklist item against it, and
writes a PASS / FAIL / REVIEW report:

  PASS    checked automatically and satisfied
  FAIL    checked automatically and not satisfied
  REVIEW  can't be decided from the XML alone (needs a human eye) — the
          reviewer at the environment gate makes that call

This script never exits non-zero because of a FAIL: the point is to put the
results in front of the reviewer at the approval gate, who decides whether
the promotion goes ahead. It exits non-zero only if it can't run at all.

Most checks are heuristics over Boomi's process XML (shape types, attributes
such as catchAll/retryCount/allowSimultaneous, shape dragpoints), so each
result names the evidence it saw. Tune the thresholds/patterns in CONFIG.

Prints `checklist-passed=`, `checklist-failed=`, `checklist-review=` and
`checklist-report=<path>` for $GITHUB_OUTPUT. The report is a markdown file the
workflow appends to the job summary.

Sub-processes: every process-call shape in the process is followed (recursively,
cycle-guarded, CONFIG["max_subprocess_depth"] deep). Each sub-process is fetched at
the version deployed in the source environment, given the SAME full checklist as the
parent (its own section in the report; sub-processes of sub-processes included), and
verified to be deployed in the target environment — a parent promoted without its
sub-process fails at runtime, so a missing/older sub-process there is a FAIL.

Usage:
    python scripts/validate_checklist.py --process-name "PR_LOAD_..." --component-id X --package-id P
        [--source-environment dev --target-environment qa]
    python scripts/validate_checklist.py --process-name "..." --xml-file process.xml   # offline
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import deque

CONFIG = {
    # checklist.md: PR_LOAD_[Domain]_[Object]_[Direction]_[Target]
    "name_pattern": r"^PR_LOAD_[A-Za-z0-9]+_[A-Za-z0-9]+_[A-Za-z0-9]+_[A-Za-z0-9]+$",
    # Shapes that don't need a label of their own.
    "unlabelled_ok": {"start", "stop", "returndocuments"},
    # A process bigger than this with no sub-process call should probably be split.
    "max_shapes_without_subprocess": 25,
    # Words that suggest test-only shapes were left in.
    "test_markers": re.compile(r"\b(test|debug|temp|tmp|todo|fixme|dummy)\b", re.I),
    # Attribute/element names that should never carry a literal credential.
    "secret_names": re.compile(r"(password|passwd|secret|token|api[_-]?key|apikey|authorization)", re.I),
    "max_script_lines": 30,
    "max_dynamic_properties": 20,
    # Hosts' connector types that count as "send a notification".
    "notify_connectors": {"mail", "slack", "teams", "http", "smtp"},
    "max_subprocess_depth": 5,
}

PASS, FAIL, REVIEW = "PASS", "FAIL", "REVIEW"
ICON = {PASS: "✅", FAIL: "❌", REVIEW: "🔎"}


def _tag(el):
    return el.tag.split("}", 1)[-1].lower()


def _all(root, tag):
    return [e for e in root.iter() if _tag(e) == tag]


def _shapes(root):
    return _all(root, "shape")


def _shape_type(shape):
    return (shape.attrib.get("shapetype") or "").lower()


def _label(shape):
    return (shape.attrib.get("userlabel") or "").strip()


def _shape_desc(shape):
    return f"{_shape_type(shape) or 'shape'} '{shape.attrib.get('name', '?')}'"


def _process_el(root):
    procs = _all(root, "process")
    return procs[0] if procs else None


def _reachable_shapes(shapes_by_name, start_shape, identifier=None):
    """Shape names reachable by following dragpoints from start_shape.

    If `identifier` is set, only the start shape's dragpoints with that
    identifier are used for the first hop (e.g. the 'error' branch of a
    try/catch); later hops follow every dragpoint.
    """
    seen, queue = set(), deque()
    for dp in _all(start_shape, "dragpoint"):
        if identifier is None or (dp.attrib.get("identifier") or "").lower() == identifier:
            if dp.attrib.get("toShape"):
                queue.append(dp.attrib["toShape"])
    while queue:
        name = queue.popleft()
        if name in seen or name not in shapes_by_name:
            continue
        seen.add(name)
        for dp in _all(shapes_by_name[name], "dragpoint"):
            if dp.attrib.get("toShape"):
                queue.append(dp.attrib["toShape"])
    return seen


# --- individual checks: each returns (status, evidence) ---------------------

def check_naming(ctx):
    name = ctx["process_name"]
    ok = re.match(CONFIG["name_pattern"], name) is not None
    return (PASS if ok else FAIL), f"`{name}` " + ("matches" if ok else "does not match") + " the convention `PR_LOAD_<Domain>_<Object>_<Direction>_<Target>`"


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _component_ref(shape):
    """True if the shape points at a Boomi component (mapId, connectionId, processId, ...).

    Boomi shows such shapes under the referenced component's own name, so no
    userlabel is needed for them to be identifiable.
    """
    return any(
        k.lower().endswith("id") and k.lower() != "id" and _UUID.match(v or "")
        for el in shape.iter()
        for k, v in el.attrib.items()
    )


def check_labels(ctx):
    missing = [
        _shape_desc(s)
        for s in _shapes(ctx["root"])
        if _shape_type(s) not in CONFIG["unlabelled_ok"] and not _label(s) and not _component_ref(s)
    ]
    if missing:
        return FAIL, f"{len(missing)} unlabelled shape(s): " + ", ".join(missing[:8]) + (" …" if len(missing) > 8 else "")
    return PASS, "every shape has a label or references a named component"


def check_subprocess(ctx):
    shapes = _shapes(ctx["root"])
    calls = [s for s in shapes if _shape_type(s) in ("processcall", "subprocess", "process")]
    if calls:
        return PASS, f"{len(calls)} sub-process call(s) in {len(shapes)} shapes"
    if len(shapes) > CONFIG["max_shapes_without_subprocess"]:
        return FAIL, f"{len(shapes)} shapes and no sub-process call — consider extracting reusable logic"
    return PASS, f"{len(shapes)} shapes — small enough not to need sub-processes"


def _subprocess_calls(root):
    """componentIds of every sub-process called from this process's process-call shapes."""
    ids = []
    for el in root.iter():
        if _tag(el) == "processcall" and el.attrib.get("processId"):
            ids.append(el.attrib["processId"])
    return list(dict.fromkeys(ids))


def _call_sites(root, owner):
    """{componentId: ["<owner> / <shape name> (<label>)", ...]} for every process-call shape."""
    sites = {}
    by_name = {sh.attrib.get("name"): sh for sh in _shapes(root)}
    start = next((sh for sh in by_name.values() if _shape_type(sh) == "start"), None)
    reachable = _reachable_shapes(by_name, start) if start is not None else None
    for shape in _shapes(root):
        for el in shape.iter():
            if _tag(el) == "processcall" and el.attrib.get("processId"):
                label, name = _label(shape), shape.attrib.get("name", "?")
                pos = f"x={shape.attrib.get('x', '?')}, y={shape.attrib.get('y', '?')}"
                if reachable is None:
                    flow = "no Start shape found"
                elif name in reachable:
                    flow = "connected to Start"
                else:
                    flow = "NOT connected to Start — orphan/hidden shape"
                sites.setdefault(el.attrib["processId"], []).append(
                    f"{owner} / shape '{name}'" + (f" ('{label}')" if label else "") + f" ({pos}; {flow})"
                )
    return sites


def check_subprocess_dependencies(ctx):
    subs = ctx["subprocesses"]
    if not subs:
        if ctx.get("subprocess_note"):
            return REVIEW, ctx["subprocess_note"]
        return PASS, "no sub-process calls"
    target, source = ctx["target_env"], ctx["source_env"]
    bad, notes = [], []
    for cid, sub in subs.items():
        label = f"'{sub['name']}'"
        if sub.get("error"):
            bad.append(f"{label} ({cid}) could not be fetched: {sub['error']}")
            continue
        deps = ctx["sub_deployments"].get(cid)
        if deps is None:
            notes.append(f"{label}: deployment state not checked")
            continue
        src, tgt = deps.get(source), deps.get(target)
        if tgt is None:
            where = f"deployed in {source}" if src else f"not deployed in {source} either"
            bad.append(f"{label} is NOT deployed in {target} ({where})")
        elif src and src.get("packageVersion") != tgt.get("packageVersion"):
            bad.append(
                f"{label} version differs: {source}={src.get('packageVersion')} vs {target}={tgt.get('packageVersion')}"
            )
    for env in ctx.get("extra_envs", ()):
        missing = [f"'{s['name']}'" for cid, s in subs.items()
                   if cid in ctx["sub_deployments"] and ctx["sub_deployments"][cid].get(env) is None]
        if missing:
            notes.append(f"not yet deployed in {env} (needed before promotion there): " + ", ".join(missing))
    sites = " | Call sites: " + "; ".join(
        f"'{s['name']}' <- {', '.join(s.get('called_from', [])) or '?'}" for s in subs.values()
    )
    if bad:
        return FAIL, "; ".join(bad) + sites
    if notes:
        return REVIEW, "; ".join(notes) + sites
    return PASS, f"{len(subs)} sub-process(es), all deployed in {target} at the {source} version" + sites


def check_description(ctx):
    for el in _all(ctx["root"], "description"):
        if (el.text or "").strip():
            return PASS, "description is populated"
    desc = ctx["root"].attrib.get("description", "").strip()
    if desc:
        return PASS, "description is populated"
    return FAIL, "component description is empty"


def check_no_hardcoded_credentials(ctx):
    hits = []
    for el in ctx["root"].iter():
        for k, v in el.attrib.items():
            if CONFIG["secret_names"].search(k) and v.strip() and not v.strip().startswith(("{", "$")):
                hits.append(f"<{_tag(el)} {k}=…>")
        if CONFIG["secret_names"].search(_tag(el)) and (el.text or "").strip():
            hits.append(f"<{_tag(el)}> has a literal value")
    for el in ctx["root"].iter():
        text = (el.text or "")
        if re.search(r"(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}", text, re.I):
            hits.append(f"literal auth header in <{_tag(el)}>")
    if hits:
        return FAIL, "possible hardcoded credential: " + "; ".join(sorted(set(hits))[:6])
    return PASS, "no credential-looking literals in the process definition"


def _scripts(root):
    out = []
    for el in root.iter():
        t = _tag(el)
        if (t == "script" or t.endswith("script") or t == "scripttoexecute") and len((el.text or "").strip()) > 5:
            out.append(el.text.strip())
    return out


def check_scripting(ctx):
    scripts = _scripts(ctx["root"])
    for map_root in ctx["map_roots"].values():
        scripts.extend(_scripts(map_root))
    if not ctx["map_roots"] and ctx["map_ids"]:
        return REVIEW, f"{len(ctx['map_ids'])} map(s) referenced but their definitions could not be fetched"
    if not scripts:
        return PASS, "no scripting found in the process or its maps"
    problems = []
    for i, s in enumerate(scripts, 1):
        lines = [l for l in s.splitlines() if l.strip()]
        if len(lines) > CONFIG["max_script_lines"]:
            problems.append(f"script #{i} is {len(lines)} lines (> {CONFIG['max_script_lines']})")
        if not re.search(r"(//|/\*|^\s*#)", s, re.M):
            problems.append(f"script #{i} has no comments")
    if problems:
        return FAIL, "; ".join(problems)
    return PASS, f"{len(scripts)} script(s), all short and commented"


def check_dynamic_properties(ctx):
    n = len(_all(ctx["root"], "documentproperty")) + len(_all(ctx["root"], "dynamicprocessproperty"))
    if n > CONFIG["max_dynamic_properties"]:
        return FAIL, f"{n} document/dynamic property settings (> {CONFIG['max_dynamic_properties']}) — likely overloaded"
    return PASS, f"{n} document/dynamic property setting(s)"


def _catch_shapes(root):
    return [s for s in _shapes(root) if _shape_type(s) in ("catcherrors", "trycatch")]


def _catch_cfg(shape):
    for el in shape.iter():
        if _tag(el) in ("catcherrors", "trycatch"):
            return el.attrib
    return {}


def check_try_catch(ctx):
    catches = _catch_shapes(ctx["root"])
    if not catches:
        return FAIL, "no Try/Catch shape"
    cfgs = [_catch_cfg(c) for c in catches]
    if any((c.get("catchAll") or "").lower() == "true" for c in cfgs):
        return PASS, "Try/Catch present with 'catch all errors' enabled"
    return FAIL, "Try/Catch present but 'catch all errors' is not enabled (document-level errors only)"


def check_notifications(ctx):
    notifies = [s for s in _shapes(ctx["root"]) if _shape_type(s) == "notify"]
    conns = [
        s for s in _shapes(ctx["root"])
        if _shape_type(s) == "connectoraction"
        and any((e.attrib.get("connectorType") or "").lower() in CONFIG["notify_connectors"] for e in s.iter())
    ]
    if notifies:
        return PASS, f"{len(notifies)} Notify shape(s)"
    if conns:
        return REVIEW, "no Notify shape; email/HTTP connector call(s) present — confirm one is the error notification"
    return FAIL, "no Notify shape or email/webhook connector call"


def check_dead_letter(ctx):
    shapes_by_name = {s.attrib.get("name"): s for s in _shapes(ctx["root"])}
    catches = _catch_shapes(ctx["root"])
    if not catches:
        return FAIL, "no Try/Catch shape, so no error path to write failed documents from"
    for c in catches:
        reach = _reachable_shapes(shapes_by_name, c, identifier="error")
        if any(_shape_type(shapes_by_name[n]) == "connectoraction" for n in reach):
            return REVIEW, "error path reaches a connector call — confirm it persists failed documents for reprocessing"
    return FAIL, "the Catch (error) path does not reach any connector, so failed documents aren't stored anywhere"


def check_retry(ctx):
    counts = []
    for c in _catch_shapes(ctx["root"]):
        try:
            counts.append(int(_catch_cfg(c).get("retryCount", "0") or 0))
        except ValueError:
            pass
    if any(n > 0 for n in counts):
        return PASS, f"Try/Catch retryCount = {max(counts)}"
    return FAIL, "no Try/Catch with retryCount > 0"


def check_test_artifacts(ctx):
    hits = [
        _shape_desc(s) + f" ('{_label(s)}')"
        for s in _shapes(ctx["root"])
        if CONFIG["test_markers"].search(_label(s))
    ]
    if hits:
        return FAIL, "test-looking shape(s): " + ", ".join(hits[:6])
    return PASS, "no test/debug/temp-labelled shapes"


# (section, item text, check fn)
CHECKLIST = [
    ("Structure & Naming", "Process name follows naming convention", check_naming),
    ("Structure & Naming", "All shapes are labelled — no unnamed shapes", check_labels),
    ("Structure & Naming", "Sub-processes used appropriately for reusable logic", check_subprocess),
    ("Structure & Naming", "All linked sub-processes (at any depth) deployed in the target environment", check_subprocess_dependencies),
    ("Structure & Naming", "Process notes / description populated", check_description),
    ("Structure & Naming", "No test data or test-only shapes left in the process", check_test_artifacts),
    ("Connectors & Connections", "Credentials in connection extensions, not hardcoded", check_no_hardcoded_credentials),
    ("Data Handling & Mapping", "Scripting in maps minimal and commented", check_scripting),
    ("Data Handling & Mapping", "Document / dynamic properties not overloaded", check_dynamic_properties),
    ("Error Handling", "Try/Catch captures all error types, not just document-level", check_try_catch),
    ("Error Handling", "Error notifications configured (email / Slack webhook)", check_notifications),
    ("Error Handling", "Failed documents go to a dead-letter path / DB", check_dead_letter),
    ("Error Handling", "Retry logic for transient / timeout failures", check_retry),
]


# How each checklist item is validated — shown at the end of the report.
# "Auto" = decided from the process XML / Boomi API; "Manual" = always left to the reviewer.
HOW_VALIDATED = {
    "Process name follows naming convention":
        ("Auto", "Process name matched against the regex `PR_LOAD_<Domain>_<Object>_<Direction>_<Target>` (CONFIG name_pattern)."),
    "All shapes are labelled — no unnamed shapes":
        ("Auto", "Every `shape` except start/stop/returndocuments needs a `userlabel` OR a reference to a component "
                 "(map, connection/operation, sub-process — Boomi displays those by the component's name)."),
    "Sub-processes used appropriately for reusable logic":
        ("Auto", "Counts process-call shapes. PASS if any exist or the process has ≤ 25 shapes; FAIL if > 25 shapes and no sub-process call."),
    "All linked sub-processes (at any depth) deployed in the target environment":
        ("Auto", "Every `processcall/@processId` is followed recursively (sub-processes of sub-processes too, up to 5 levels). Each is fetched at the "
                 "version deployed in the source env and must have an active deployment in the target env at the same version (FAIL otherwise); "
                 "missing in the next env (prod) is flagged for review. Each linked process then gets this whole checklist in its own section."),
    "Process notes / description populated":
        ("Auto", "Component `description` element/attribute must be non-empty."),
    "No test data or test-only shapes left in the process":
        ("Auto", "No shape label may contain test/debug/temp/tmp/todo/fixme/dummy as a whole word."),
    "Credentials in connection extensions, not hardcoded":
        ("Auto", "Scans every attribute/element whose name looks like password/secret/token/api-key/authorization for a literal value, "
                 "and text for literal `Bearer`/`Basic` headers. Values starting with `{` or `$` (variables) are allowed."),
    "Scripting in maps minimal and commented":
        ("Auto", "Fetches each referenced map and the process's own scripts; FAIL if a script is > 30 non-blank lines or has no comments. "
                 "Needs validation if maps were referenced but could not be fetched."),
    "Document / dynamic properties not overloaded":
        ("Auto", "Counts document-property and dynamic-process-property settings; FAIL if > 20."),
    "Try/Catch captures all error types, not just document-level":
        ("Auto", "Needs a Try/Catch shape with `catchAll=true`; FAIL if none or catch-all is off."),
    "Error notifications configured (email / Slack webhook)":
        ("Auto", "PASS if a Notify shape exists; needs validation if only an email/HTTP/Slack/Teams connector call exists; FAIL if neither."),
    "Failed documents go to a dead-letter path / DB":
        ("Auto", "Follows the Try/Catch `error` branch; needs validation if it reaches a connector call (reviewer confirms it persists failures), "
                 "FAIL if it reaches none or there is no Try/Catch."),
    "Retry logic for transient / timeout failures":
        ("Auto", "Needs a Try/Catch with `retryCount` > 0."),
}

STATUS_TEXT = {PASS: "✅ Passed", FAIL: "❌ Not passed", REVIEW: "🔎 Needs validation"}


def _run_items(ctx):
    results = []
    for section, item, fn in CHECKLIST:
        if fn is check_subprocess_dependencies and (
            ctx.get("is_subprocess") or not (ctx["subprocesses"] or ctx.get("subprocess_note"))
        ):
            continue  # evaluated once, on the parent, and only if it links to sub-processes
        try:
            status, evidence = fn(ctx)
        except Exception as exc:  # a broken heuristic must not hide the other results
            status, evidence = REVIEW, f"check errored ({type(exc).__name__}: {exc})"
        results.append({"section": section, "item": item, "status": status, "evidence": evidence})
    return results


def run_checklist(ctx):
    """Parent first, then each sub-process separately. Every row carries its `subject`."""
    subject = f"Parent process: {ctx['process_name']}"
    results = [dict(r, subject=subject) for r in _run_items(ctx)]
    sub_base = dict(ctx, is_subprocess=True)
    for cid, sub in ctx["subprocesses"].items():
        caller = (sub.get("called_from") or ["?"])[0].split(" / ")[0]
        level = "Sub-process" if sub["depth"] == 1 else f"Sub-process (level {sub['depth']})"
        subject = f"{level}: {sub['name']} (`{cid}`) — called by {caller}"
        if sub.get("error"):
            results.append({"subject": subject, "section": "Structure & Naming", "item": "Sub-process definition fetched",
                            "status": FAIL, "evidence": sub["error"]})
            continue
        sub_ctx = dict(sub_base, root=sub["root"], map_roots=sub["map_roots"], map_ids=sub["map_ids"],
                       process_name=sub["name"])
        results += [dict(r, subject=subject) for r in _run_items(sub_ctx)]
    return results


def _counts(rows):
    return (sum(r["status"] == PASS for r in rows), sum(r["status"] == FAIL for r in rows),
            sum(r["status"] == REVIEW for r in rows))


def _esc(text):
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(results, title):
    p, f, v = _counts(results)
    total = len(results)
    if f:
        verdict = f"> ## ❌ {f} checklist item(s) not passed — reviewer decision required"
    elif v:
        verdict = "> ## 🔎 No automated failures — items needing validation remain"
    else:
        verdict = "> ## ✅ All checklist items passed"
    filled = round(10 * p / total) if total else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    subjects = list(dict.fromkeys(r["subject"] for r in results))

    lines = [
        "## 🧾 Pre-QA Checklist Validation",
        "",
        f"**Subject:** {title}",
        "",
        verdict,
        "",
        f"{bar} **{p} of {total}** items passed across the parent and {len(subjects) - 1} sub-process(es)",
        "",
        "| Process | ✅ Passed | ❌ Not passed | 🔎 Needs validation | Overall |",
        "|---|:-:|:-:|:-:|:-:|",
    ]
    for sub in subjects:
        sp, sf, sv = _counts([r for r in results if r["subject"] == sub])
        lines.append(f"| {sub} | {sp} | {sf} | {sv} | {ICON[FAIL] if sf else ICON[REVIEW] if sv else ICON[PASS]} |")

    for sub in subjects:
        rows = [r for r in results if r["subject"] == sub]
        sp, sf, sv = _counts(rows)
        lines += ["", "---", "", f"## {sub}", "", f"✅ {sp} passed · ❌ {sf} not passed · 🔎 {sv} need validation", "",
                  "| Section | Checklist item | Status | Why |", "|---|---|:-:|---|"]
        lines += [f"| {r['section']} | {r['item']} | {STATUS_TEXT[r['status']]} | {_esc(r['evidence'])} |" for r in rows]

    lines += ["", "---", "", "## 📖 How each checklist item is validated", "",
              "_Applied identically to the parent and to every sub-process. **Auto** = decided by the script from the "
              "component XML / Boomi API; **Manual** = always left to the reviewer._", "",
              "| Checklist item | Type | How it is validated |", "|---|:-:|---|"]
    seen = set()
    for r in results:
        if r["item"] in seen:
            continue
        seen.add(r["item"])
        kind, how = HOW_VALIDATED.get(r["item"], ("—", "—"))
        lines.append(f"| {r['item']} | {kind} | {_esc(how)} |")
    lines += [
        "",
        "_Failed and review items do not block the run. The reviewer decides at the "
        "**Review pending deployments** prompt whether to approve the QA deployment._",
    ]
    return "\n".join(lines) + "\n"


def _parse(xml_text):
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"Could not parse component XML: {exc}", file=sys.stderr)
        return None


def _load_env_id(environment):
    path = os.path.join(os.path.dirname(__file__), "..", "environments", f"{environment}.json")
    with open(path) as f:
        return json.load(f)["environment_id"]


def _collect_maps(root, client, sink_roots, sink_ids):
    for el in root.iter():
        map_id = el.attrib.get("mapId") if _tag(el) == "map" else None
        if map_id:
            sink_ids.add(map_id)
    if client is None:
        return
    for map_id in sorted(sink_ids):
        if map_id in sink_roots:
            continue
        try:
            parsed = _parse(client.get_component_xml(map_id))
            if parsed is not None:
                sink_roots[map_id] = parsed
        except Exception as exc:
            print(f"Could not fetch map {map_id}: {exc}", file=sys.stderr)


def _fetch_subprocess_xml(client, cid, source_dep):
    """Component XML at the version deployed in the source env, else the latest."""
    if source_dep:
        version = next(
            (p.get("componentVersion") for p in client.list_packages(cid, limit=200)
             if p.get("packageId") == source_dep.get("packageId")),
            None,
        )
        if version is not None:
            return client.get_component_xml(cid, version=version)
    return client.get_component_xml(cid)


def load_subprocesses(root, client, source_env, target_env, extra_envs=()):
    """Follow process calls recursively. Returns ({cid: info}, {cid: {env: deployment|None}})."""
    subs, deployments, env_cache = {}, {}, {}

    def find(env, cid):
        if env not in env_cache:
            env_cache[env] = client.list_deployed_packages(_load_env_id(env))
        recs = [r for r in env_cache[env]
                if r.get("componentId") == cid and str(r.get("active", True)).lower() != "false"]
        recs.sort(key=lambda r: r.get("deployedDate", ""), reverse=True)
        return recs[0] if recs else None

    parent_name = root.attrib.get("name") or "parent"
    queue = deque((cid, 1, sites) for cid, sites in _call_sites(root, parent_name).items())
    while queue:
        cid, depth, sites = queue.popleft()
        if cid in subs:  # also guards against call cycles
            continue
        info = {"name": cid, "depth": depth, "map_roots": {}, "map_ids": set(), "called_from": sites}
        subs[cid] = info
        try:
            deployments[cid] = {env: find(env, cid) for env in (source_env, target_env, *extra_envs)}
        except Exception as exc:
            print(f"Could not read deployments for sub-process {cid}: {exc}", file=sys.stderr)
        try:
            sub_root = _parse(_fetch_subprocess_xml(client, cid, (deployments.get(cid) or {}).get(source_env)))
            if sub_root is None:
                raise ValueError("component XML did not parse")
        except Exception as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
            continue
        info["root"] = sub_root
        info["name"] = sub_root.attrib.get("name") or cid
        _collect_maps(sub_root, client, info["map_roots"], info["map_ids"])
        nested = _call_sites(sub_root, info["name"])
        if depth < CONFIG["max_subprocess_depth"]:
            queue.extend((c, depth + 1, sites) for c, sites in nested.items())
        elif nested:
            print(f"Sub-process '{info['name']}' calls further sub-processes beyond depth "
                  f"{CONFIG['max_subprocess_depth']}; not followed.", file=sys.stderr)
    return subs, deployments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-name", required=True)
    parser.add_argument("--component-id", default="")
    parser.add_argument("--package-id", default="")
    parser.add_argument("--xml-file", default="", help="offline: validate this XML file instead of calling Boomi")
    parser.add_argument("--source-environment", default="dev", help="environment the package is promoted from")
    parser.add_argument("--target-environment", default="qa", help="environment the package is promoted to")
    parser.add_argument("--also-check-environment", action="append", default=[],
                        help="further environment(s) the package will reach next (e.g. prod); sub-processes missing there are flagged for review")
    parser.add_argument("--out", default="checklist_report.md")
    args = parser.parse_args()

    ctx = {"process_name": args.process_name, "map_roots": {}, "map_ids": set(),
           "subprocesses": {}, "sub_deployments": {}, "source_env": args.source_environment,
           "target_env": args.target_environment, "extra_envs": args.also_check_environment}
    client = None

    if args.xml_file:
        with open(args.xml_file, encoding="utf-8") as f:
            xml_text = f.read()
        title = os.path.basename(args.xml_file)
    else:
        if not args.component_id or not args.package_id:
            parser.error("--component-id and --package-id are required unless --xml-file is given")
        from boomi_client import BoomiClient
        client = BoomiClient()
        version = next(
            (p.get("componentVersion") for p in client.list_packages(args.component_id, limit=200)
             if p.get("packageId") == args.package_id),
            None,
        )
        if version is None:
            print(f"Could not resolve a componentVersion for package {args.package_id}", file=sys.stderr)
            sys.exit(1)
        xml_text = client.get_component_xml(args.component_id, version=version)
        title = f"{args.process_name} (package {args.package_id}, component v{version})"


    root = _parse(xml_text)
    if root is None:
        sys.exit(1)
    ctx["root"] = root

    _collect_maps(root, client, ctx["map_roots"], ctx["map_ids"])
    if client is not None:
        ctx["subprocesses"], ctx["sub_deployments"] = load_subprocesses(
            root, client, args.source_environment, args.target_environment, args.also_check_environment
        )
    elif _subprocess_calls(root):
        ctx["subprocess_note"] = f"{len(_subprocess_calls(root))} sub-process call(s) found but not fetched (offline run)"

    results = run_checklist(ctx)
    report = render_markdown(results, title)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)

    for status, key in ((PASS, "passed"), (FAIL, "failed"), (REVIEW, "review")):
        print(f"checklist-{key}={sum(1 for r in results if r['status'] == status)}")
    print(f"checklist-report={args.out}")
    sys.stderr.reconfigure(encoding="utf-8")
    print(report, file=sys.stderr)


if __name__ == "__main__":
    main()
