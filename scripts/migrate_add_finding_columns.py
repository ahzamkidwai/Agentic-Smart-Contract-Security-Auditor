"""
One-off migration: adds the columns FindingRecord gained (related_finding_ids,
severity_rationale, applicability_note) without touching existing rows.

SQLModel/SQLAlchemy's `create_all()` only creates tables that don't exist —
it never ALTERs an existing table when its model definition changes. Since
`findingrecord` already existed from earlier runs, the new columns were
never added to the actual .db file, causing:
    sqlite3.OperationalError: no such column: findingrecord.related_finding_ids

Run this once, from the project root, with your app venv active:
    python scripts/migrate_add_finding_columns.py

Safe to run multiple times — it checks for each column's existence first.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from config import settings

# settings.DATABASE_URL is like "sqlite:///C:\...\data\audit_explainer.db"
DB_PATH = Path(settings.DATABASE_URL.replace("sqlite:///", "", 1))

# (column_name, SQL type, default)
NEW_COLUMNS = [
    ("related_finding_ids", "JSON", "'[]'"),
    ("severity_rationale", "TEXT", "''"),
    ("applicability_note", "TEXT", "''"),
]


def main() -> None:
    if not DB_PATH.exists():
        print(f"No existing database at {DB_PATH} — nothing to migrate. "
              f"init_db() will create the full current schema on next app start.")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(findingrecord)")}

        added = []
        for name, sql_type, default in NEW_COLUMNS:
            if name in existing:
                print(f"  [skip] {name} already present")
                continue
            conn.execute(
                f'ALTER TABLE findingrecord ADD COLUMN {name} {sql_type} DEFAULT {default}'
            )
            added.append(name)
            print(f"  [added] {name} ({sql_type})")

        conn.commit()
        if added:
            print(f"\nMigration complete — added {len(added)} column(s) to {DB_PATH}")
        else:
            print("\nSchema already up to date — no changes made.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()