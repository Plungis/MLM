# MyAnonaSuite Python Runtime

This directory contains the MyAnonaSuite runtime and its active HeavyMLM and
MAM-Spender modules.
The `mlm-python` command and existing data paths remain unchanged for backward
compatibility.

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

The HeavyMLM organizer can split multi-book torrents into individual library
books. It recognizes distinct book folders, flat ebook collections, and flat
M4B/M4A/MP4 collections, reads embedded EPUB/audio metadata when available,
and leaves ambiguous disc/chapter/track layouts intact. The behavior is enabled
by `split_collections = true` and can be changed live from Configuration.

## Use MAM-Spender

Open `http://localhost:3157/suite/mam-spender/dashboard` or choose **M$ / Spend**
in the suite switcher. Its Dashboard, Graph, History, All MaM Data, and Config
pages are part of the same process, database, and authenticated MaM session as
HeavyMLM. Frequently adjusted purchase policy, point buffer, and schedule
controls are available directly on the Dashboard and on the Config page. The
Config page also owns Session_ID/import tools and identifies the exact running
suite version and listener.

To migrate the standalone MAM-Spender Web Edition, open **MAM-Spender →
Config**, copy the contents of its `data/config.json` into **Import old
config.json**, and choose **Import Web Edition data**. This imports module
settings, totals, run history, spending events, cached MaM data, bonus history,
and any plain Session_ID. The original file is not changed.

## ABSidekick module

Choose **A_ / ABS** in the suite switcher to open the integrated ABSidekick
organizer. Its own sidebar exposes Run Center, Review Desk, Targeting, Matching,
Tags & Actions, and Config. Config stores the Audiobookshelf URL, library,
provider, and optional remembered API token; all policy changes apply to the
next preview or run without restarting MyAnonaSuite.

ABSidekick retains the source project's dry-run preview, weighted scoring,
metadata-patch, ABS quick-match and tags-only modes, author/tag/path targeting,
pause/resume/cancel, retries, live searchable logs, cover comparisons, and
manual approve/reject workflow. Its state is stored in an `absidekick` folder
beside the configured SQLite database, separate from HeavyMLM and MAM-Spender.

## Publish the request portal on a custom domain

The Configuration screen can enable a separate request portal without exposing
the HeavyMLM dashboard on that hostname. Set the request domain, a portal title,
a shared requester username/password, and a per-client request limit there.
Changes apply immediately. The password is never written to `config.toml` in
plain text; MyAnonaSuite saves a salted PBKDF2-SHA256 hash. The old shared access
code remains available under **Legacy shared access code** for upgraded installs.

Point an HTTPS reverse proxy at the MyAnonaSuite service and preserve the
original `Host` header. For example, a minimal Caddy site is:

```caddyfile
requests.example.com {
    reverse_proxy 127.0.0.1:3157
}
```

Then set `request_portal_domains = ["requests.example.com"]` and enable the
portal. That hostname serves only the request form and static assets; dashboard,
configuration, API documentation, and approval routes return 404. Do not expose
port 3157 directly to the public internet. Configure requester credentials, or
put an authentication layer such as Cloudflare Access in front of the domain.

Administrators can open the portal in a new tab from **Requests → Request
Portal** in the sidebar or the **Open request portal** button in the request
inbox. On the admin machine, that direct preview remains available even while
the external hostname requires a login.

Visitors can combine title, author, series, narrator, format, category,
language, availability, seeder, and sort filters. They can also paste a public
Goodreads book URL to prefill its metadata and run the search. Submissions never
download automatically: they enter the Requests inbox for approval, and approval
revalidates the MaM release before adding it to the normal download queue.
