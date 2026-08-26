"""
scripts/migrate_add_project_tables.py

Adds the project-audit schema on top of your existing SQLite DB:
  - Creates AuditJob and AuditFile tables (via SQLModel.metadata, so their
    definition stays in sync with app/models/project.py - no hand-written
    CREATE TABLE to drift out of date).
  - Adds a nullable `file_id` column to your existing `finding` table (or
    whatever your single-file Finding table is actually called - see
    FINDING_TABLE_NAME below, ADJUST THIS to match your real table name)
    so findings from a project-wide run can be linked back to an AuditFile.
    Findings from your existing single-file pipeline keep file_id = NULL.

Destination: scripts/migrate_add_project_tables.py
Runs under: .venv-app (needs sqlmodel + your app package importable)

Idempotent: safe to re-run. Checks for existing tables/columns via
PRAGMA before creating/altering anything, same principle as not blindly
re-running the earlier solidity_known_bugs.json / SWC ingestion steps.

Usage (Git Bash, from repo root):
    .venv-app/Scripts/python.exe scripts/migrate_add_project_tables.py

Add a --db path argument if your DB path isn't picked up from your existing
app config - see DB_PATH resolution below.
"""

import sqlite3
import sys
from pathlib import Path

# --- Adjust these two to match your actual codebase -----------------------
FINDING_TABLE_NAME = "finding"          # your existing single-file findings table
DB_PATH_CANDIDATES = [
    Path("audit_explainer.db"),
    Path("app.db"),
    Path("data/audit_explainer.db"),
]
# ---------------------------------------------------------------------------


def resolve_db_path() -> Path:
    for candidate in DB_PATH_CANDIDATES:
        if candidate.exists():
            return candidate
    # Fall back to importing your app's engine config if none of the
    # guessed paths exist - uncomment and adjust if you have one:
    #
    # from app.db import DATABASE_URL
    # return Path(DATABASE_URL.replace("sqlite:///", ""))
    print(
        "Could not locate the SQLite DB file. Edit DB_PATH_CANDIDATES in "
        "this script, or hardcode the path directly.",
        file=sys.stderr,
    )
    sys.exit(1)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r[1] == column_name for r in rows)


def create_project_tables(db_path: Path) -> None:
    """
    Uses SQLModel.metadata.create_all so AuditJob/AuditFile are created with
    exactly the schema defined in app/models/project.py - avoids a second,
    hand-maintained source of truth that can drift.
    """
    from sqlmodel import SQLModel, create_engine
    # Importing the models registers them on SQLModel.metadata
    from app.models.project import AuditJob, AuditFile  # noqa: F401

    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SQLModel.metadata.tables["auditjob"],
            SQLModel.metadata.tables["auditfile"],
        ],
    )
    print("✓ auditjob and auditfile tables ready")


def add_file_id_to_finding(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, FINDING_TABLE_NAME):
        print(
            f"⚠ table '{FINDING_TABLE_NAME}' not found - skipping file_id "
            f"column addition. If your Finding table has a different name, "
            f"set FINDING_TABLE_NAME at the top of this script and re-run."
        )
        return

    if column_exists(conn, FINDING_TABLE_NAME, "file_id"):
        print(f"✓ {FINDING_TABLE_NAME}.file_id already exists, skipping")
        return

    # SQLite ALTER TABLE ADD COLUMN doesn't enforce FK constraints on
    # existing rows (fine here since it's nullable and all existing rows
    # get NULL). Foreign key enforcement for new rows still applies if
    # PRAGMA foreign_keys=ON is set by your app at connect time.
    conn.execute(
        f"ALTER TABLE {FINDING_TABLE_NAME} "
        f"ADD COLUMN file_id INTEGER REFERENCES auditfile(id)"
    )
    conn.commit()
    print(f"✓ added {FINDING_TABLE_NAME}.file_id (nullable FK -> auditfile.id)")


def create_index(conn: sqlite3.Connection) -> None:
    # auditjob / auditfile always exist by this point (created in step 1),
    # so these two are unconditional.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_auditjob_job_id ON auditjob(job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auditfile_job_id ON auditfile(job_id)"
    )

    # The finding-table index depends on file_id having been added, which
    # itself depends on FINDING_TABLE_NAME being correct - skip cleanly
    # rather than crash if that table wasn't found/named right.
    if table_exists(conn, FINDING_TABLE_NAME) and column_exists(conn, FINDING_TABLE_NAME, "file_id"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{FINDING_TABLE_NAME}_file_id "
            f"ON {FINDING_TABLE_NAME}(file_id)"
        )
        print(f"✓ index on {FINDING_TABLE_NAME}.file_id ensured")
    else:
        print(
            f"⚠ skipped index on {FINDING_TABLE_NAME}.file_id "
            f"(table/column not present - fix FINDING_TABLE_NAME and re-run)"
        )

    conn.commit()
    print("✓ auditjob / auditfile indexes ensured")


def main():
    db_path = resolve_db_path()
    print(f"Migrating: {db_path.resolve()}")

    # Step 1: create auditjob / auditfile tables via SQLModel metadata
    create_project_tables(db_path)

    # Step 2: raw sqlite3 for the ALTER TABLE + index steps, since SQLModel
    # doesn't do in-place ALTER for existing tables
    conn = sqlite3.connect(str(db_path))
    try:
        add_file_id_to_finding(conn)
        create_index(conn)
    finally:
        conn.close()

    print("Migration complete.")


if __name__ == "__main__":
    main()