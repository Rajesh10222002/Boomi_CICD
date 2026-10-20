"""
Local connectivity check for the extension-values store in Oracle (POC).

Connects with python-oracledb (thin mode — no Oracle client install needed) as
the given read-only user, reads the given view, and prints what's there WITHOUT
printing any value: only environment / type / component / field / secret flag and
the length of each value. The password is prompted for (hidden), never taken from
a file or the command line, and never printed.

Usage:
    pip install oracledb
    python scripts/test_oracle_connection.py --user qa_reader --view cicd_owner.qa_extension_values
    python scripts/test_oracle_connection.py --user pd_reader --view cicd_owner.pd_extension_values
    # isolation check — this one is EXPECTED to fail with ORA-00942 for qa_reader:
    python scripts/test_oracle_connection.py --user qa_reader --view cicd_owner.pd_extension_values
"""

import argparse
import getpass
import os
import re
import sys

import oracledb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--view", required=True, help="owner.view, e.g. cicd_owner.qa_extension_values")
    parser.add_argument("--host", default=os.environ.get("ORA_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ORA_PORT", "1521")))
    parser.add_argument("--service", default=os.environ.get("ORA_SERVICE", "XEPDB1"))
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_$#]+(\.[A-Za-z0-9_$#]+)?", args.view):
        raise SystemExit("--view must look like owner.view_name")

    password = os.environ.get("ORA_PASSWORD") or getpass.getpass(f"Password for {args.user}: ")
    dsn = f"{args.host}:{args.port}/{args.service}"
    try:
        conn = oracledb.connect(user=args.user, password=password, dsn=dsn)
    except oracledb.Error as exc:
        print(f"Could not connect to {dsn} as {args.user}: {exc}", file=sys.stderr)
        sys.exit(1)

    with conn:
        print(f"Connected to {dsn} as {args.user} (server {conn.version}).")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT environment, extension_type, component_name, component_id, field_name, is_secret, "
                    f"LENGTH(value_text) FROM {args.view} ORDER BY component_name, field_name"
                )
                rows = cur.fetchall()
        except oracledb.Error as exc:
            print(f"Query on {args.view} failed: {exc}", file=sys.stderr)
            sys.exit(2)

    print(f"{len(rows)} row(s) in {args.view} (values not shown):")
    for env, etype, name, cid, field, secret, length in rows:
        print(f"  [{env}] {etype} | {name or '-'} ({cid}) | {field} | secret={bool(secret)} | value length={length}")


if __name__ == "__main__":
    main()
