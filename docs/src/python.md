# Python

Install Python 3.11 or newer, clone this repository, then run:

```powershell
python -m pip install -e .\python
mlm-python run --config .\config.toml --database .\data.sqlite3
```

Open <http://localhost:3157> for the web UI.

If using the Windows qBittorrent version, also remember to enable its Web UI under settings.

Configure MLM to connect to qbittorent with a configuration like:

```toml
[[qbittorrent]]
url = "http://localhost:8080"
username = "qbittorent username"
password = "qbittorent password"
```

Make sure the port number (8080) matches the port configured in qBittorrent, as well as the username and password. Or leave those out if "Bypass authentication for clients on localhost" is checked.

For migration from the old application database, see
[`python/README.md`](../../python/README.md).
