# MyAnonaSuite

MyAnonaSuite is a shared terminal-style home for MyAnonamouse and audiobook
library tools. HeavyMLM and MAM-Spender are active modules; the ABSidekick
workspace remains reserved for its future port.

The suite shell and the three tools stay separated at the code boundary. Shared
code is limited to navigation, theming, and explicit cross-module interfaces.
HeavyMLM-specific presentation code and templates live under dedicated
`modules/heavymlm` and `modules/mam_spender` namespaces. Each module owns its
policy, persistence, scheduler, and presentation while deliberately sharing the
suite shell, SQLite runtime, and authenticated MaM client.

## MAM-Spender module

The MAM-Spender Web Edition v1.4 feature set is incorporated into the suite:

- scheduled and manual spending scans, with a two-minute minimum interval;
- upload-credit purchases in 50 GiB / 25,000-point blocks, capped at three
  blocks per run;
- Freeleech Wedge-only and alternating wedge/upload modes;
- a configurable point reserve and optional VIP renewal at 83 days remaining;
- balance verification, detailed failure traces, cumulative totals, local run
  history, and confirmed spending events;
- pie, bar, and cumulative timeline analytics;
- MaM account data, notifications, bonus history, local/MaM clocks, Vault reset,
  and Lotto reset/drawing countdowns;
- Session_ID import from raw values, Cookie headers, Netscape/curl files, and
  browser JSON exports; and
- its original Green, Ember, Modern, and Mouse module themes; and
- one-step import of the standalone Web Edition `data/config.json`, including
  settings, totals, history, cached account data, and its plain stored
  Session_ID.

Open **M$ / Spend** in the bottom suite switcher. Dedicated Dashboard, Graph,
History, All MaM Data, and Config pages are available under
`/suite/mam-spender/`. MAM-Spender uses the same
approved API session and server process as HeavyMLM, so it does not open a
second port or duplicate secrets. Purchases are irreversible; a purchase is
recorded only when MaM explicitly reports success or the follow-up point balance
confirms the expected store charge.

HeavyMLM is both an auto downloader and a library organizer. Both parts are optional so either can be replaced with e.g. RSS or [booktree](https://github.com/myxdvz/booktree) if you prefer. And even if you use both, you can still add torrents manually and have them organized, and/or use booktree for collections or files that are not from MaM.

This allows you to automatically download for example bookmarks and have them hardlinked into an organized library folder for e.g. ABS. It also follows a list of preferred formats so that if you first download the mp3 version of a book and then later download an m4b, the mp3 will be automatically removed from your library and optionally moved to a different category or tagged in qBittorrent.

The auto downloader can both use configured searches similar to RSS, and Goodreads lists as input. It keeps track of your unsat slots and will by default always leave at least 10 open. You can also set a direct filled-slot ceiling, reserve freeleech wedges, and prefer wedges before ratio downloads. Operational policy and scheduler settings can be saved and applied live from the Configuration screen. List imports can optionally track and grab both audiobook and ebook editions independently. It also keeps track of your library and avoids downloading e.g. an mp3 torrent if you already have the m4b.

The library organizer will only link one audio file type and one ebook file type per torrent. So e.g. an audiobook torrent with both m4b and pdf files will have both linked, but an ebook torrent with both and epub and mobi will only have the epub linked.

Organizer runs expose a live, expandable background trace on the dashboard,
including completion checks, category routing, metadata lookups, source and
destination paths, file placement, skips, and failures. Large file copies run
outside the web request loop, SQLite permits concurrent UI reads, and record
views are paginated to keep navigation responsive while background work runs.
The organizer asks qBittorrent only for categories explicitly named by
`[[library]]` rules; unrelated categories never enter the scan. A full
inventory query is used only when a library intentionally matches by
`download_dir` instead of category.

MaM search metadata uses nested JSON strings for fields such as authors,
narrators, series, and categories. The Python runtime decodes both those live
API values and already-decoded structures, matching the legacy Rust client.

Freeleech state fields are normalized from MaM's string booleans before wedge
decisions. Downloader diagnostics record the raw flags, normalized decision,
and a distinct successful wedge-application event for verification.

Stored downloader failures have a dedicated recovery center that preserves the
exact exception, explains the likely cause, provides concrete next steps, and
offers retry, diagnostics, configuration, and dismiss actions.

Limitations:

- At the moment HeavyMLM only works with qbittorrent
- HeavyMLM works with torrents, meaning collections (multiple books in a single torrents) will be treated as one book (however if you link these with [booktree](https://github.com/myxdvz/booktree), HeavyMLM will not touch those files)
- HeavyMLM works with torrents from MaM, meaning files not via a torrent from here can not be handled (however if you link these with [booktree](https://github.com/myxdvz/booktree), HeavyMLM will not touch those files)

The application runtime is now Python 3.11+ and is available as a Docker
container or an installable Python package.

## Run with Docker

```shell
docker compose up --build -d
```

Copy `config.example.toml` to `./config/config.toml` and edit it. Data is
stored under `./data`, and the example Compose file mounts `./library` at
`/library`.

## Run with Python

```shell
python -m pip install -e ./python
mlm-python run --config ./config.toml --database ./data.sqlite3
```

The web UI listens on port 3157 by default. MyAnonaSuite can also serve a
dedicated request portal on a configured custom domain. The public hostname is
isolated from administrative routes, supports the full multi-filter MaM search
and Goodreads book-link lookup, and sends every submission to an approval inbox
instead of downloading automatically. See [python/README.md](python/README.md)
for reverse-proxy and security setup.

## Migrate legacy data

The Python migrator imports the versioned JSON export into SQLite while
preserving every source record, making an automatic backup, checking record
counts, and running SQLite's integrity check before installing the new database.
See [the Python migration guide](python/README.md).

Existing configuration fields and aliases remain supported. Full configuration
documentation is under [`docs/src`](docs/src).
