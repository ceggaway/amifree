"""The paste-once migration bundle stays complete and in order."""

import re
from pathlib import Path

from app.db.supabase import SCHEMA_VERSION
from scripts.migrations_bundle import bundle

MIGRATIONS = Path(__file__).parents[1] / "app" / "db" / "migrations"


def test_bundle_has_every_migration_in_order():
    text = bundle()
    names = re.findall(r"^-- ===== (\S+) =====$", text, re.MULTILINE)
    assert names == ["schema.sql"] + sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[1].startswith("001_") and len(names) == len(set(names))


def test_every_migration_says_it_is_safe_to_reapply():
    for path in MIGRATIONS.glob("*.sql"):
        assert "safe to reapply" in path.read_text().splitlines()[0], path.name


def test_the_bundle_reaches_the_version_the_app_needs():
    versions = [
        int(v)
        for v in re.findall(r"schema_migrations\(version,\s*name\)\s*values\s*\((\d+)", bundle())
    ]
    assert max(versions) == SCHEMA_VERSION
