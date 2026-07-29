# MLM - Myanonamouse Library Manager

MLM is both an auto downloader and a library organizer. Both parts are optional so either can be replaced with e.g. RSS or [booktree](https://github.com/myxdvz/booktree) if you prefer. And even if you use both, you can still add torrents manually and have them organized, and/or use booktree for collections or files that are not from MaM.

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

Limitations:

- At the moment MLM only works with qbittorrent
- MLM works with torrents, meaning collections (multiple books in a single torrents) will be treated as one book (however if you link these with [booktree](https://github.com/myxdvz/booktree), MLM will not touch those files)
- MLM works with torrents from MaM, meaning files not via a torrent from here can not be handled (however if you link these with [booktree](https://github.com/myxdvz/booktree), MLM will not touch those files)

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

The web UI listens on port 3157 by default.

## Migrate legacy data

The Python migrator imports the versioned JSON export into SQLite while
preserving every source record, making an automatic backup, checking record
counts, and running SQLite's integrity check before installing the new database.
See [the Python migration guide](python/README.md).

Existing configuration fields and aliases remain supported. Full configuration
documentation is under [`docs/src`](docs/src).
