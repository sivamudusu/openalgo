#!/usr/bin/env python3
"""
Migration: Visual Chart Orders (draggable bracket orders on /trading)

Adds the two tables backing the visual order tool:

- ``vb_bracket``       one row per staged or live visual bracket
- ``vb_bracket_event`` append-only audit trail for every state change

It also adds the ``strategy`` column that lets the manual ATM ticket keep its
``Manual`` tag while reusing the bracket engine. Existing rows keep the original
``VisualBracket`` tag.

``init_db()`` in ``database/visual_bracket_db.py`` creates the tables too, but
that only helps an installation whose database is opened after this ships. This
script is what reaches deployments that upgrade with ``git pull``.

Idempotent - safe to run multiple times.

Usage:
    cd upgrade
    uv run migrate_visual_bracket.py           # Apply migration
    uv run migrate_visual_bracket.py --status  # Check status without changing anything
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import _pragmas  # noqa: F401,E402
from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

TABLES = ("vb_bracket", "vb_bracket_event")
STRATEGY_COLUMN = "strategy"


def resolve_sqlite_path(db_url: str) -> str:
    """Make a relative sqlite:/// path absolute against the project root."""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return db_url
    path = db_url[len(prefix) :]
    if not path or path == ":memory:" or os.path.isabs(path):
        return db_url
    return prefix + os.path.join(PROJECT_ROOT, path)


def get_database_url() -> str:
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    return resolve_sqlite_path(os.getenv("DATABASE_URL", "sqlite:///db/openalgo.db"))


def get_engine():
    url = get_database_url()
    if "sqlite" in url:
        return create_engine(url, poolclass=NullPool, connect_args={"check_same_thread": False})
    return create_engine(url, pool_size=5, max_overflow=10)


def table_exists(engine, table_name: str) -> bool:
    return table_name in inspect(engine).get_table_names()


def ensure_strategy_column(engine) -> bool:
    """Add the strategy column to an existing vb_bracket table."""
    if not table_exists(engine, "vb_bracket"):
        print("  [SKIP] vb_bracket is not present")
        return False

    columns = {column["name"] for column in inspect(engine).get_columns("vb_bracket")}
    if STRATEGY_COLUMN in columns:
        print("  [SKIP] vb_bracket.strategy already exists")
        return True

    print("  [ALTER] Adding vb_bracket.strategy...")
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE vb_bracket "
                "ADD COLUMN strategy VARCHAR(32) NOT NULL DEFAULT 'VisualBracket'"
            )
        )
    print("  [OK] vb_bracket.strategy added")
    return True


def migrate(engine) -> bool:
    """Create both tables when missing and add the strategy column."""
    from database.visual_bracket_db import Base

    missing = [table for table in TABLES if not table_exists(engine, table)]
    if missing:
        print(f"  [CREATE] Creating {', '.join(missing)}...")
        Base.metadata.create_all(bind=engine, checkfirst=True)
        print("  [OK] visual bracket tables created")
    else:
        print("  [SKIP] visual bracket tables already exist")

    return ensure_strategy_column(engine)


def status(engine) -> bool:
    """Report what exists without changing anything."""
    print()
    print("Visual bracket migration status")
    print("-" * 46)

    applied = True
    for table in TABLES:
        present = table_exists(engine, table)
        print(f"  {table:<30} {'present' if present else 'MISSING'}")
        applied = applied and present
        if present and table == "vb_bracket":
            columns = {column["name"] for column in inspect(engine).get_columns(table)}
            has_strategy = STRATEGY_COLUMN in columns
            print(f"  {'vb_bracket.strategy':<30} {'present' if has_strategy else 'MISSING'}")
            applied = applied and has_strategy

    print()
    print(f"  {'APPLIED' if applied else 'NOT APPLIED'}")
    print()
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Visual bracket order tables")
    parser.add_argument(
        "--status", action="store_true", help="Show status without changing anything"
    )
    args = parser.parse_args()

    engine = get_engine()
    if args.status:
        return 0 if status(engine) else 1

    ok = migrate(engine)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
