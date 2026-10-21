"""
Add or update ONE value in the extension value store (Oracle), encrypting secrets.

Run locally by whoever owns the store. Prompts (hidden) for the owner's database password and
for the value — nothing sensitive goes on the command line, in a file or in the output. Needs
SECRETS_KEY (the same key the deploy job uses to decrypt) when a value is a secret.

You identify things by NAME. For a connection the Boomi component id is looked up and stored
automatically when BOOMI_* credentials are available in your environment; otherwise the row is
stored un-pinned (matched by name) and the plan reports "matched by name".

Examples:
    # a connection field
    python scripts/seed_extension_values.py --environment qa --name "CICD Extension Demo Connection" --field password --secret
    # a dynamic process property (scope it to a process so other processes' same-named properties are untouched)
    python scripts/seed_extension_values.py --environment qa --type DYNAMIC_PROPERTY --name DPP_GREETING --process "PR_LOAD_CICD_DynProp_Inbound_Demo"
    # a defined process-property component: field = the property key
    python scripts/seed_extension_values.py --environment pd --type PROCESS_PROPERTY --name "Settings" --field region
    # a cross-reference table cell: field = column (ref1, ref2 ...), --row = row number
    python scripts/seed_extension_values.py --environment qa --type CROSS_REFERENCE --name "Codes" --field ref2 --row 1
    # revert a field to the component's own value
    python scripts/seed_extension_values.py --environment qa --name "My Connection" --field url --use-default
"""

import argparse
import getpass
import os

from extension_store import encrypt, store_env

PLACEHOLDER_ID = "REPLACE-WITH-CONNECTION-ID"
TYPES = ["CONNECTION", "DYNAMIC_PROPERTY", "PROCESS_PROPERTY", "CROSS_REFERENCE"]
BOOMI_TYPE = {"CONNECTION": "connector-settings"}  # Boomi component type used to look the id up

MERGE = """
MERGE INTO extension_values t
USING (SELECT :environment environment, :etype extension_type, :cid component_id, :field field_name,
              :sub_key sub_key, :row_no row_no, :process_name process_name FROM dual) s
ON (t.environment = s.environment AND t.extension_type = s.extension_type AND t.component_id = s.component_id
    AND t.field_name = s.field_name AND NVL(t.sub_key, '-') = NVL(s.sub_key, '-')
    AND NVL(t.row_no, 0) = NVL(s.row_no, 0) AND NVL(t.process_name, '-') = NVL(s.process_name, '-'))
WHEN MATCHED THEN UPDATE SET value_text = :val, is_secret = :secret, component_name = :cname,
     use_default = :use_default, connector_type = :ctype, is_active = 1, updated_at = SYSTIMESTAMP, updated_by = :by
WHEN NOT MATCHED THEN INSERT (environment, extension_type, component_id, component_name, field_name, sub_key, row_no,
     process_name, connector_type, use_default, value_text, is_secret, updated_by)
     VALUES (:environment, :etype, :cid, :cname, :field, :sub_key, :row_no, :process_name, :ctype, :use_default,
             :val, :secret, :by)
"""


def resolve_component_id(etype, name):
    """Boomi component id for a connection name, or None (needs BOOMI_* env vars; never raises)."""
    if etype not in BOOMI_TYPE or not os.environ.get("BOOMI_API_TOKEN"):
        return None
    try:
        from boomi_client import BoomiClient

        return BoomiClient().find_component_by_name(name, component_type=BOOMI_TYPE[etype])
    except Exception as exc:  # name not found, ambiguous, or no access: store un-pinned
        print(f"(could not look up the id for '{name}': {exc})")
        return None


def upsert(cur, *, environment, etype, name, field, value, secret=False, component_id="", sub_key=None, row_no=None,
           process_name=None, connector_type=None, use_default=False):
    cid = component_id or (name if etype != "CONNECTION" else PLACEHOLDER_ID)
    stored = "" if use_default else (encrypt(value) if secret else value)
    if not stored and not use_default:
        raise ValueError("empty value — nothing stored")
    cur.execute(MERGE, dict(environment=store_env(environment), etype=etype, cid=cid, field=field, sub_key=sub_key,
                            row_no=row_no, process_name=process_name, val=stored or " ", secret=1 if secret else 0,
                            cname=name, use_default=1 if use_default else 0, ctype=connector_type,
                            by=os.environ.get("USERNAME") or os.environ.get("USER") or "seed"))


def connect(args_user, args_dsn):
    import oracledb

    password = os.environ.get("EXT_SEED_PASSWORD") or getpass.getpass(f"Password for {args_user}: ")
    return oracledb.connect(user=args_user, password=password, dsn=args_dsn)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True, choices=["qa", "pd", "prod"])
    parser.add_argument("--type", default="CONNECTION", choices=TYPES)
    parser.add_argument("--name", "--connection-name", dest="name", required=True, help="component / property name")
    parser.add_argument("--component-id", "--connection-id", dest="component_id", default="")
    parser.add_argument("--field", default="value", help="field / process-property key / cross-reference column")
    parser.add_argument("--row", type=int, default=None, help="cross-reference table row number (1..n)")
    parser.add_argument("--sub-key", default=None)
    parser.add_argument("--process", dest="process_name", default=None, help="apply only when this process is promoted")
    parser.add_argument("--connector-type", default=None)
    parser.add_argument("--secret", action="store_true", help="encrypt the value (use for passwords/tokens)")
    parser.add_argument("--use-default", action="store_true", help="revert the field to the component's own value")
    parser.add_argument("--owner-user", default=os.environ.get("EXT_SEED_USER", "cicd_owner"))
    parser.add_argument("--dsn", default=os.environ.get("EXT_DB_DSN", "localhost:1521/XEPDB1"))
    args = parser.parse_args()

    cid = args.component_id or resolve_component_id(args.type, args.name) or ""
    value = ""
    if not args.use_default:
        value = getpass.getpass("Value (hidden): ") if args.secret else input("Value: ")
    with connect(args.owner_user, args.dsn) as conn, conn.cursor() as cur:
        upsert(cur, environment=args.environment, etype=args.type, name=args.name, field=args.field, value=value,
               secret=args.secret, component_id=cid, sub_key=args.sub_key, row_no=args.row, process_name=args.process_name,
               connector_type=args.connector_type, use_default=args.use_default)
        conn.commit()
    pinned = "id pinned" if cid else "matched by name (id not pinned)"
    print(f"Stored {args.type} '{args.name}' field '{args.field}' for {store_env(args.environment)} "
          f"({'revert-to-default' if args.use_default else 'encrypted' if args.secret else 'plain'}; {pinned}).")


if __name__ == "__main__":
    main()
