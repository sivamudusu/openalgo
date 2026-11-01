#!/usr/bin/env python
"""Sahasra exact model tables.

Creates the three tables used by the assisted Sahasra exact panel:

- sahasra_exact_snapshots
- sahasra_exact_chain
- sahasra_exact_context

The migration is idempotent. ``--status`` reports the tables without creating
anything.

Usage:
    cd upgrade && uv run migrate_sahasra_exact.py
    cd upgrade && uv run migrate_sahasra_exact.py --status
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import _pragmas  # noqa: F401,E402
from dotenv import load_dotenv
from sqlalchemy import inspect


def resolve_sqlite_path(db_url):
    """Make a relative sqlite:/// path absolute against the project root."""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return db_url
    path = db_url[len(prefix) :]
    if not path or path == ":memory:" or os.path.isabs(path):
        return db_url
    return prefix + os.path.join(PROJECT_ROOT, path)


load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
os.environ["DATABASE_URL"] = resolve_sqlite_path(
    os.getenv("DATABASE_URL") or "sqlite:///db/openalgo.db"
)

from database.sahasra_exact_db import Base, engine  # noqa: E402
from utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

TABLES = ("sahasra_exact_snapshots", "sahasra_exact_chain", "sahasra_exact_context")


def status():
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    for table in TABLES:
        print(f"{table}: {'present' if table in existing else 'missing'}")
    return all(table in existing for table in TABLES)


def migrate():
    Base.metadata.create_all(bind=engine)
    print("Sahasra exact tables created or already present.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Create the Sahasra exact model tables.")
    parser.add_argument(
        "--status", action="store_true", help="Report status without changing anything."
    )
    args = parser.parse_args()
    if args.status:
        status()
        return 0
    try:
        migrate()
        status()
        return 0
    except Exception:
        logger.exception("Sahasra exact migration failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
