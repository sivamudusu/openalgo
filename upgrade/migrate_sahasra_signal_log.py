#!/usr/bin/env python3
"""
Migration: Sahasra signal log

Adds the ``sahasra_signal`` table, which records every signal the model
produced together with the level map that produced it, and what the signal
turned into.

The point is evidence. A rule that has never been measured is a belief, and
the platform's own standard is that a model is judged on fifty or more live
trades rather than on one session's backtest.

``init_db()`` in ``database/sahasra_signal_db.py`` creates the table too, but
that only helps an installation whose database is opened after this ships.
This script is what reaches deployments that upgrade with ``git pull``.

Idempotent - safe to run multiple times.

Usage:
    cd upgrade
    uv run migrate_sahasra_signal_log.py           # Apply migration
    uv run migrate_sahasra_signal_log.py --status  # Check status without changing anything
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import _pragmas  # noqa: F401,E402
from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

TABLE = "sahasra_signal"


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


def migrate(engine) -> bool:
    """Create the table when it is missing."""
    from database.sahasra_signal_db import Base

    if table_exists(engine, TABLE):
        print(f"  [SKIP] {TABLE} already exists")
        return True

    print(f"  [CREATE] Creating {TABLE}...")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    print(f"  [OK] {TABLE} created")
    return True


def status(engine) -> bool:
    """Report what exists without changing anything."""
    print()
    print("Sahasra signal log migration status")
    print("-" * 44)

    present = table_exists(engine, TABLE)
    print(f"  {TABLE:<30} {'present' if present else 'MISSING'}")

    print()
    print(f"  {'APPLIED' if present else 'NOT APPLIED'}")
    print()
    return present


def main() -> int:
    parser = argparse.ArgumentParser(description="Sahasra signal log table")
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
