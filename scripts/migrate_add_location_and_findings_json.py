"""
One-off migration: adds

  findingrecord.file_name              TEXT
  findingrecord.start_line             INTEGER
  findingrecord.end_line               INTEGER
  findingrecord.fix_already_present    BOOLEAN
  findingrecord.evidence               JSON
  auditfile.findings_json              JSON

without touching existing rows. Same rationale as
migrate_add_finding_columns.py: SQLModel/SQLAlchemy's create_all() never
ALTERs an existing table when its model definition changes.

Run once, from the project root, with your app venv active:
    python scripts/migrate_add_location_and_findings_json.py

Safe to run multiple times — it checks for each column's existence first.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config import settings

DB_PATH = Path(settings.DATABASE_URL.replace("sqlite:///", "", 1))

FINDINGRECORD_COLUMNS = [
    ("file_name", "TEXT", "NULL"),
    ("start_line", "INTEGER", "NULL"),
    ("end_line", "INTEGER", "NULL"),
    ("fix_already_present", "BOOLEAN", "0"),
    ("evidence", "JSON", "'{}'"),
]

AUDITFILE_COLUMNS = [
    ("findings_json", "JSON", "'[]'"),
]


def _add_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str, str]]) -> list[str]:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    added = []
    for name, sql_type, default in columns:
        if name in existing:
            print(f"  [skip] {table}.{name} already present")
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type} DEFAULT {default}")
        added.append(name)
        print(f"  [added] {table}.{name} ({sql_type})")
    return added


def main() -> None:
    if not DB_PATH.exists():
        print(
            f"No existing database at {DB_PATH} — nothing to migrate. "
            f"init_db() will create the full current schema on next app start."
        )
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        added = []
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        if "findingrecord" in tables:
            added += _add_columns(conn, "findingrecord", FINDINGRECORD_COLUMNS)
        else:
            print("  [skip] findingrecord table does not exist yet")

        if "auditfile" in tables:
            added += _add_columns(conn, "auditfile", AUDITFILE_COLUMNS)
        else:
            print("  [skip] auditfile table does not exist yet")

        conn.commit()
        if added:
            print(f"\nMigration complete — added {len(added)} column(s) to {DB_PATH}")
        else:
            print("\nSchema already up to date — no changes made.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()