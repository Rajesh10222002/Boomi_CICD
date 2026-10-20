"""
Add or update ONE value in the extension value store (Oracle), encrypting secrets.

Run locally by whoever owns the store. Prompts (hidden) for the owner's database
password and for the value — nothing sensitive goes on the command line, in a file,
or in the output. Needs SECRETS_KEY (the same key the deploy job uses to decrypt).

Usage:
    python scripts/seed_extension_values.py --environment qa --connection-name "CICD Extension Demo Connection" --field password --secret
    python scripts/seed_extension_values.py --environment pd --connection-id <uuid> --connection-name "..." --field user
    # --connection-id may be omitted while a connection hasn't been probed yet: the row then
    # matches by connection NAME until you re-run with the real id (see the Extension Probe).
"""

import argparse
import getpass
import os

import oracledb

from extension_store import encrypt, store_env

PLACEHOLDER_ID = "REPLACE-WITH-CONNECTION-ID"

MERGE = """
MERGE INTO extension_values t
USING (SELECT :environment environment, :etype extension_type, :cid component_id, :field field_name FROM dual) s
ON (t.environment = s.environment AND t.extension_type = s.extension_type
    AND t.component_id = s.component_id AND t.field_name = s.field_name)
WHEN MATCHED THEN UPDATE SET value_text = :val, is_secret = :secret, component_name = :cname,
     is_active = 1, updated_at = SYSTIMESTAMP, updated_by = :by
WHEN NOT MATCHED THEN INSERT (environment, extension_type, component_id, component_name, field_name,
     value_text, is_secret, updated_by)
     VALUES (:environment, :etype, :cid, :cname, :field, :val, :secret, :by)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True, choices=["qa", "pd", "prod"])
    parser.add_argument("--connection-name", required=True)
    parser.add_argument("--connection-id", default=PLACEHOLDER_ID)
    parser.add_argument("--field", required=True, help="Boomi extension field id, e.g. url, user, password")
    parser.add_argument("--type", default="CONNECTION")
    parser.add_argument("--secret", action="store_true", help="encrypt the value (use for passwords/tokens)")
    parser.add_argument("--owner-user", default=os.environ.get("EXT_SEED_USER", "cicd_owner"))
    parser.add_argument("--dsn", default=os.environ.get("EXT_DB_DSN", "localhost:1521/XEPDB1"))
    args = parser.parse_args()

    env = store_env(args.environment)
    password = os.environ.get("EXT_SEED_PASSWORD") or getpass.getpass(f"Password for {args.owner_user}: ")
    value = getpass.getpass("Value (hidden): ") if args.secret else input("Value: ")
    if not value:
        raise SystemExit("Empty value — nothing stored.")
    stored = encrypt(value) if args.secret else value

    with oracledb.connect(user=args.owner_user, password=password, dsn=args.dsn) as conn, conn.cursor() as cur:
        cur.execute(MERGE, dict(environment=env, etype=args.type, cid=args.connection_id, field=args.field,
                                val=stored, secret=1 if args.secret else 0, cname=args.connection_name,
                                by=os.environ.get("USERNAME") or os.environ.get("USER") or "seed"))
        conn.commit()
    print(f"Stored {args.type} {args.field!r} for {args.connection_name!r} in {env} "
          f"({'encrypted' if args.secret else 'plain'}).")


if __name__ == "__main__":
    main()
