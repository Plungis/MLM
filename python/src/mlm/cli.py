from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .migration import MigrationError, migrate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mlm-python")
    subparsers = parser.add_subparsers(dest="command", required=True)
    migration = subparsers.add_parser(
        "migrate", help="back up and migrate a legacy native_db database to SQLite"
    )
    migration.add_argument("--source-db", required=True, type=Path)
    migration.add_argument("--destination", required=True, type=Path)
    source = migration.add_mutually_exclusive_group(required=True)
    source.add_argument("--legacy-executable", type=Path)
    source.add_argument("--export-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = migrate(
            args.source_db,
            args.destination,
            export_json=args.export_json,
            legacy_executable=args.legacy_executable,
        )
    except MigrationError as error:
        print(f"Migration failed: {error}", file=sys.stderr)
        return 1

    print(f"Migrated database: {result.destination}")
    print(f"Original database backup: {result.source_backup}")
    for table, count in result.counts.items():
        print(f"  {table}: {count}")
    print(f"Export SHA-256: {result.export_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
