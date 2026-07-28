# MLM Python

This directory contains the compatibility-first Python migration of MLM.

The first implemented slice is a lossless database migration:

1. The legacy Rust executable exports its `native_db` database to versioned JSON.
2. The Python migrator backs up the original database.
3. Every record is inserted into SQLite with its complete canonical JSON payload.
4. Record counts and SQLite integrity are validated before the temporary database
   atomically replaces the destination.

## Migrate an existing database

Build the legacy exporter once:

```powershell
cargo build --release -p mlm
```

Then run:

```powershell
cd python
python -m pip install -e .
mlm-python migrate `
  --source-db "$env:LOCALAPPDATA\MLM\data.db" `
  --destination "$env:LOCALAPPDATA\MLM\data.sqlite3" `
  --legacy-executable "..\target\release\mlm.exe"
```

The command exports from the backup copy, not the live database. You can also
pass `--export-json path\to\export.json` instead of `--legacy-executable`.
