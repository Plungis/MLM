from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .downloader import grab_selected_torrents
from .mam import MamClient
from .migration import MigrationError, migrate
from .qbittorrent import QbitClient
from .repository import Repository


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
    downloader = subparsers.add_parser(
        "download", help="process migrated pending torrents once"
    )
    downloader.add_argument("--config", required=True, type=Path)
    downloader.add_argument("--database", required=True, type=Path)
    run = subparsers.add_parser("run", help="start the Python MLM service and web UI")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--database", required=True, type=Path)
    return parser


async def _download(config_path: Path, database_path: Path) -> int:
    config = load_config(config_path)
    if not config.qbittorrent:
        raise ConfigError("at least one [[qbittorrent]] entry is required")
    repository = Repository(database_path)
    async with MamClient(
        repository.config_value("mam_id") or config.mam_id,
        cookie_store=lambda value: repository.set_config_value("mam_id", value),
    ) as mam:
        await mam.check_mam_id()
        qbits: list[QbitClient] = []
        try:
            for definition in config.qbittorrent:
                qbit = QbitClient(definition.url)
                await qbit.login(definition.username, definition.password)
                qbits.append(qbit)
            result = await grab_selected_torrents(
                config, repository, mam, qbits[0], other_qbits=qbits[1:]
            )
        finally:
            for qbit in qbits:
                await qbit.close()
    print(
        f"Download run: {result.downloaded} downloaded, "
        f"{result.failed} failed, {result.skipped} skipped"
    )
    return 1 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        try:
            import uvicorn

            from .web import create_app

            config = load_config(args.config)
            uvicorn.run(
                create_app(args.config, args.database),
                host=config.web_host,
                port=config.web_port,
            )
            return 0
        except (ConfigError, OSError) as error:
            print(f"Startup failed: {error}", file=sys.stderr)
            return 1
    if args.command == "download":
        try:
            return asyncio.run(_download(args.config, args.database))
        except (ConfigError, OSError) as error:
            print(f"Download failed: {error}", file=sys.stderr)
            return 1

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
