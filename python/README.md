# MLM Python

This directory contains the compatibility-first Python migration of MLM.

The first implemented slice is a lossless database migration:

1. The legacy Rust executable exports its `native_db` database to versioned JSON.
2. The Python migrator backs up the original database.
3. Every record is inserted into SQLite with its complete canonical JSON payload.
4. Record counts and SQLite integrity are validated before the temporary database
   atomically replaces the destination.

## Migrate an existing database

Check out and build the frozen one-time legacy exporter:

```powershell
git clone --branch legacy-export-v1 https://github.com/Plungis/MLM legacy-export
cd legacy-export
cargo build --release -p mlm
cd ..
```

Then run:

```powershell
cd python
python -m pip install -e .
mlm-python migrate `
  --source-db "$env:LOCALAPPDATA\MLM\data.db" `
  --destination "$env:LOCALAPPDATA\MLM\data.sqlite3" `
  --legacy-executable ".\legacy-export\target\release\mlm.exe"
```

The old executable is used only to decode its private `native_db` format. It
exports from the automatic backup copy, not the live database, and is never
used by the new application runtime. You can also pass
`--export-json path\to\export.json` instead of `--legacy-executable`.

## Process migrated pending downloads

```powershell
mlm-python download `
  --config "$env:APPDATA\MLM\config.toml" `
  --database "$env:LOCALAPPDATA\MLM\data.sqlite3"
```

This command currently performs one downloader pass. It validates the MaM
cookie, logs into the first configured qBittorrent server, includes the required
`tid` on every MaM download, and records successes or errors in SQLite.

## Run the Python service

```powershell
mlm-python run `
  --config "$env:APPDATA\MLM\config.toml" `
  --database "$env:LOCALAPPDATA\MLM\data.sqlite3"
```

The service runs the configured autograbbers, Goodreads and Notion imports,
pending downloader, qBittorrent library organizers, and duplicate cleaner on
their configured intervals. The dashboard listens on `web_host:web_port`.
