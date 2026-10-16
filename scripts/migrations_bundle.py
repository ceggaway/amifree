"""Print the schema and every migration, in order, as one script for the Supabase SQL Editor.

    python scripts/migrations_bundle.py > app/db/all_migrations.sql

The output is committed as app/db/all_migrations.sql, so whoever runs the database can copy
it straight from GitHub. A test fails if it falls behind the migrations folder.

The schema and each migration are safe to reapply, so running the whole bundle brings any
database (a new project included) up to the version this code needs, whatever it had before.
The app refuses to start until it has.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "db"


def bundle() -> str:
    parts = [f"-- ===== schema.sql =====\n{(ROOT / 'schema.sql').read_text().strip()}\n"]
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        parts.append(f"-- ===== {path.name} =====\n{path.read_text().strip()}\n")
    return "\n".join(parts)


if __name__ == "__main__":
    sys.stdout.write(bundle())
