"""
Structural diff between two Boomi packages' underlying component
definitions — described in plain terms (element added/removed, attribute
changed from X to Y), not a raw line-by-line XML diff. Supplements the
version/packageId-level "diff" line describe_current.py/
resolve_current_package.py already build for the tracking issue.

Confirmed against a real account: PackagedComponent does expose a
componentVersion field, and Component GET does accept
"{componentId}~{version}" for a historical revision (see
BoomiClient.get_component_xml) — kept failing soft anyway (prints
`xml-diff-available=false` and a reason instead of raising) in case
either behaves differently on some other account.

Boomi returns component XML minified onto a single line, so a raw text
diff is useless (the whole document reads as "one line changed"). This
instead parses both documents and walks them together: child elements
are matched up by tag + an identifying attribute (name/id/key, in that
order) where one exists, or positionally within same-tag groups where it
doesn't, and differences are reported as plain-language bullets (e.g.
"shape 'map1' -> mapping: attribute 'toField' changed from 'dst.name' to
'dst.fullName'") rather than XML syntax. This describes the tree
structure generically — it doesn't know what a Boomi shape/connector
*means*, just what changed in the document.

--from-package-id is optional: omit it for a first-ever deploy (nothing
to diff against). --to-package-id is optional: omit it for ci.yml's
"pending build" case, where the new package doesn't exist yet — this then
diffs against the component's current (unpinned) live definition instead,
i.e. "what's about to be packaged."

On success, prints `xml-diff-available=true`, writes the change list to
--out, and appends it (truncated) to $GITHUB_STEP_SUMMARY if set. If
either document fails to parse as XML, falls back to a raw pretty-printed
line diff instead of the structural summary.

Usage:
    python scripts/diff_component_versions.py --component-id X --from-package-id A --to-package-id B
    python scripts/diff_component_versions.py --component-id X --from-package-id A   # to = current live XML
    python scripts/diff_component_versions.py --component-id X                        # first deploy, to = current live XML
"""

import argparse
import difflib
import os
import sys
import xml.dom.minidom
import xml.etree.ElementTree as ET

from boomi_client import BoomiApiError, BoomiClient

PREVIEW_LINES = 400
IDENTIFYING_ATTRS = ("name", "id", "key")


def _component_version_for_package(client, component_id, package_id):
    for p in client.list_packages(component_id, limit=200):
        if p.get("packageId") == package_id:
            return p.get("componentVersion")
    return None


def _strip_ns(tag):
    return tag.split("}", 1)[-1] if tag.startswith("{") else tag


def _identify(elem):
    """Short human label for one element, e.g. shape 'map1', or just its tag if it has no identifying attribute."""
    tag = _strip_ns(elem.tag)
    for key in IDENTIFYING_ATTRS:
        if key in elem.attrib:
            return f"{tag} '{elem.attrib[key]}'"
    return tag


def _child_key(elem):
    """Key used to match a child between the two trees, or None if it has no identifying attribute (matched positionally instead)."""
    for key in IDENTIFYING_ATTRS:
        if key in elem.attrib:
            return (_strip_ns(elem.tag), key, elem.attrib[key])
    return None


def _diff_elements(from_el, to_el, path, changes):
    from_attrs = from_el.attrib if from_el is not None else {}
    to_attrs = to_el.attrib if to_el is not None else {}
    for key in sorted(set(from_attrs) | set(to_attrs)):
        old, new = from_attrs.get(key), to_attrs.get(key)
        if old == new:
            continue
        if old is None:
            changes.append(f"{path}: attribute '{key}' added = '{new}'")
        elif new is None:
            changes.append(f"{path}: attribute '{key}' removed (was '{old}')")
        else:
            changes.append(f"{path}: attribute '{key}' changed from '{old}' to '{new}'")

    from_children = list(from_el) if from_el is not None else []
    to_children = list(to_el) if to_el is not None else []
    if not from_children and not to_children:
        old_text = (from_el.text or "").strip() if from_el is not None else ""
        new_text = (to_el.text or "").strip() if to_el is not None else ""
        if old_text != new_text and (old_text or new_text):
            changes.append(f"{path}: value changed from '{old_text}' to '{new_text}'")

    from_named, from_unnamed = {}, []
    for c in from_children:
        k = _child_key(c)
        if k:
            from_named[k] = c
        else:
            from_unnamed.append(c)

    to_named, to_unnamed = {}, []
    for c in to_children:
        k = _child_key(c)
        if k:
            to_named[k] = c
        else:
            to_unnamed.append(c)

    for k, c in from_named.items():
        if k not in to_named:
            changes.append(f"{path} -> {_identify(c)}: removed")
    for k, c in to_named.items():
        if k not in from_named:
            changes.append(f"{path} -> {_identify(c)}: added")
    for k in from_named:
        if k in to_named:
            _diff_elements(from_named[k], to_named[k], f"{path} -> {_identify(to_named[k])}", changes)

    from_by_tag, to_by_tag = {}, {}
    for c in from_unnamed:
        from_by_tag.setdefault(_strip_ns(c.tag), []).append(c)
    for c in to_unnamed:
        to_by_tag.setdefault(_strip_ns(c.tag), []).append(c)
    for tag in sorted(set(from_by_tag) | set(to_by_tag)):
        froms, tos = from_by_tag.get(tag, []), to_by_tag.get(tag, [])
        if len(froms) != len(tos):
            changes.append(f"{path}: number of <{tag}> elements changed from {len(froms)} to {len(tos)}")
        for i in range(min(len(froms), len(tos))):
            _diff_elements(froms[i], tos[i], f"{path} -> {tag}[{i}]", changes)


def _structural_diff(from_xml, to_xml):
    """Returns a list of change bullets, or None if either document didn't parse as XML."""
    try:
        from_root = ET.fromstring(from_xml) if from_xml.strip() else None
        to_root = ET.fromstring(to_xml) if to_xml.strip() else None
    except ET.ParseError:
        return None
    if from_root is None and to_root is None:
        return []
    changes = []
    root_label = _identify(to_root if to_root is not None else from_root)
    _diff_elements(from_root, to_root, root_label, changes)
    return changes


def _pretty(xml_text):
    """Fallback: indent minified XML into one node per line for a line diff, if structural parsing fails."""
    if not xml_text.strip():
        return xml_text
    try:
        pretty = xml.dom.minidom.parseString(xml_text).toprettyxml(indent="  ")
    except Exception:
        return xml_text
    return "\n".join(line for line in pretty.splitlines() if line.strip()) + "\n"


def _fail(reason):
    print("xml-diff-available=false")
    print(reason, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--component-id", required=True)
    parser.add_argument("--from-package-id", default="")
    parser.add_argument("--to-package-id", default="")
    parser.add_argument("--out", default="component_diff.txt")
    args = parser.parse_args()

    client = BoomiClient()

    from_label = "(nothing — first deploy)"
    from_xml = ""
    if args.from_package_id:
        from_version = _component_version_for_package(client, args.component_id, args.from_package_id)
        if from_version is None:
            _fail(
                f"Could not resolve a componentVersion for package {args.from_package_id} — "
                "PackagedComponent may not expose that field on this account."
            )
            return
        try:
            from_xml = client.get_component_xml(args.component_id, version=from_version)
        except BoomiApiError as exc:
            _fail(f"Fetching component XML for version {from_version} failed: {exc}")
            return
        from_label = f"package {args.from_package_id} (component v{from_version})"

    to_label = "current live definition"
    if args.to_package_id:
        to_version = _component_version_for_package(client, args.component_id, args.to_package_id)
        if to_version is None:
            _fail(
                f"Could not resolve a componentVersion for package {args.to_package_id} — "
                "PackagedComponent may not expose that field on this account."
            )
            return
        to_label = f"package {args.to_package_id} (component v{to_version})"
    else:
        to_version = None

    try:
        to_xml = client.get_component_xml(args.component_id, version=to_version)
    except BoomiApiError as exc:
        _fail(f"Fetching component XML ({to_label}) failed: {exc}")
        return

    changes = _structural_diff(from_xml, to_xml)

    print("xml-diff-available=true")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if changes is not None:
        with open(args.out, "w") as f:
            f.write(f"{from_label} -> {to_label}\n\n")
            if changes:
                f.write("\n".join(changes) + "\n")
            else:
                f.write("(no differences)\n")
        print(f"{len(changes)} change(s) written to {args.out}", file=sys.stderr)

        if summary_path:
            preview = changes[:PREVIEW_LINES]
            with open(summary_path, "a") as f:
                f.write(f"\n<details><summary>Process diff: {from_label} -> {to_label}</summary>\n\n")
                if not changes:
                    f.write("(no differences)\n")
                else:
                    for line in preview:
                        f.write(f"- {line}\n")
                    if len(changes) > len(preview):
                        f.write(f"\n... truncated, {len(changes) - len(preview)} more change(s) — see the {args.out} artifact\n")
                f.write("\n</details>\n")
        return

    # Fallback: one or both documents didn't parse as XML — a raw pretty-printed line diff beats nothing.
    diff_lines = list(
        difflib.unified_diff(
            _pretty(from_xml).splitlines(keepends=True),
            _pretty(to_xml).splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
    )
    with open(args.out, "w") as f:
        f.writelines(diff_lines)
    print(f"Structural diff unavailable (XML didn't parse) — wrote a raw line diff to {args.out} instead", file=sys.stderr)

    if summary_path:
        preview = diff_lines[:PREVIEW_LINES]
        with open(summary_path, "a") as f:
            f.write(f"\n<details><summary>Component XML diff (raw — structural parse failed): {from_label} -> {to_label}</summary>\n\n")
            if not diff_lines:
                f.write("(no differences)\n")
            else:
                f.write("```diff\n")
                f.writelines(preview)
                if len(diff_lines) > len(preview):
                    f.write(f"\n... truncated, {len(diff_lines) - len(preview)} more line(s) — see the {args.out} artifact\n")
                f.write("```\n")
            f.write("\n</details>\n")


if __name__ == "__main__":
    main()
