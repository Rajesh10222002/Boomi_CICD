"""
Best-effort content diff between two Boomi packages' underlying component
XML — not just their version labels/packageIds (that metadata-level
comparison is what describe_current.py / resolve_current_package.py
already provide for the tracking-issue "diff" line; this supplements it).

UNVERIFIED against a real Boomi account. This depends on two things that
haven't been confirmed against real API responses:
  1. PackagedComponent exposing a `componentVersion` field identifying
     which numbered revision of the component was packaged.
  2. Component GET accepting "{componentId}~{version}" to fetch that
     specific historical revision (see BoomiClient.get_component_xml).

If either assumption doesn't hold, this fails soft: it prints
`xml-diff-available=false` and a reason to stderr, and produces no diff,
rather than failing the calling workflow. Run it against a real
promotion and check the output/errors — that's what confirms or corrects
the assumptions above.

--from-package-id is optional: omit it for a first-ever deploy (nothing
to diff against). --to-package-id is optional: omit it for ci.yml's
"pending build" case, where the new package doesn't exist yet — this then
diffs against the component's current (unpinned) live definition instead,
i.e. "what's about to be packaged."

On success, prints `xml-diff-available=true`, writes the full diff to
--out, and appends a truncated preview to $GITHUB_STEP_SUMMARY if set.

Usage:
    python scripts/diff_component_versions.py --component-id X --from-package-id A --to-package-id B
    python scripts/diff_component_versions.py --component-id X --from-package-id A   # to = current live XML
    python scripts/diff_component_versions.py --component-id X                        # first deploy, to = current live XML
"""

import argparse
import difflib
import os
import sys

from boomi_client import BoomiApiError, BoomiClient

PREVIEW_LINES = 200


def _component_version_for_package(client, component_id, package_id):
    for p in client.list_packages(component_id, limit=200):
        if p.get("packageId") == package_id:
            return p.get("componentVersion")
    return None


def _fail(reason):
    print("xml-diff-available=false")
    print(reason, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--component-id", required=True)
    parser.add_argument("--from-package-id", default="")
    parser.add_argument("--to-package-id", default="")
    parser.add_argument("--out", default="component_diff.patch")
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

    diff_lines = list(
        difflib.unified_diff(
            from_xml.splitlines(keepends=True),
            to_xml.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
    )

    with open(args.out, "w") as f:
        f.writelines(diff_lines)

    print("xml-diff-available=true")
    print(f"{len(diff_lines)} diff line(s) written to {args.out}", file=sys.stderr)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        preview = diff_lines[:PREVIEW_LINES]
        with open(summary_path, "a") as f:
            f.write(f"\n<details><summary>Component XML diff: {from_label} -> {to_label}</summary>\n\n")
            if not diff_lines:
                f.write("(no XML differences)\n")
            else:
                f.write("```diff\n")
                f.writelines(preview)
                if len(diff_lines) > len(preview):
                    f.write(f"\n... truncated, {len(diff_lines) - len(preview)} more line(s) — see the {args.out} artifact\n")
                f.write("```\n")
            f.write("\n</details>\n")


if __name__ == "__main__":
    main()
