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

Config also includes an optional native Google Books provider. Create a Google
Cloud project, enable the Books API, create an API key, restrict that key to
the Books API, paste it into **Google Books API Key**, and choose **Test &
Enable**. The in-app guide links directly to each Google Cloud screen and
explains optional stable-IP restrictions. A pasted key is saved privately but
Google remains disabled until the live test succeeds; when no tested key is
present, MyAnonaSuite sends no request to Google. The key itself is never
returned to the browser or sent to Audiobookshelf. Once the key is tested,
previews, initial matching runs, and Review Desk scans always try the selected
Audiobookshelf metadata provider first. If that ABS result does not pass the
automatic-match policy for any reason, native Google Books is queried
immediately as a second pass. This does not depend on the optional extra-provider
fallback setting. The live log displays each ABS and Google attempt, including
zero-result and skipped searches, its result count, the selected provider, and
the exact score or safety gate that led to Review. Reviewer-controlled
Google searches remain available on each Review Desk item. Temporary Google
timeouts, rate limits, and 5xx responses receive one immediate retry and do not
disable the provider after a single failed item. Three consecutive exhausted
transient searches open the run-local circuit breaker; authentication or API
configuration rejections still stop Google immediately. Job diagnostics state
whether the next item will retry or the provider was disabled and include the
underlying error.

Native Open Library search is enabled by default as the third lookup. If the
Audiobookshelf provider and the tested Google provider do not produce an
automatic match, MyAnonaSuite queries Open Library's official JSON Search API.
No Open Library API key is required. Add a contact email under **Config -> Open
Library Search** to identify the installation as Open Library requests for
regular API clients; identified clients are limited to three requests per
second and anonymous clients to one. MyAnonaSuite enforces the matching limit,
caches repeated searches for the current run, requests only the fields used by
the matcher, and gives transient failures the same one-retry/three-strike
circuit-breaker treatment as Google. The provider can be disabled completely,
and it is also available directly in Review Desk manual searches.

The winner-margin check compares only meaningfully different works. Duplicate
provider listings with the same normalized title and verified author are
treated as one logical match, and the candidate with richer evidence is
preferred. A tie still requires Review when candidates disagree on the title,
author, collection status, or series sequence.

ABSidekick retains the source project's dry-run preview, weighted scoring,
metadata-patch, ABS quick-match and tags-only modes, author/tag/path targeting,
pause/resume/cancel, retries, live searchable logs, cover comparisons, and
manual approve/reject workflow. Its state is stored in an `absidekick` folder
beside the configured SQLite database, separate from HeavyMLM and MAM-Spender.

High-precision matching is enabled by default. The engine normalizes Unicode,
punctuation, file formats, edition labels, and recognized series packaging;
compares only metadata present on both sides; treats exact ASIN/ISBN matches as
strong evidence; and blocks automatic writes on contradictory identifiers,
authors, series positions, collection status, duration, or an ambiguous
runner-up. Adaptive search cleans common filename numbering and retries a
title-only query only when the precise query is empty. Every decision, evidence
signal, search source, and conflict is visible in previews, logs, and the Review
Desk. These safeguards and their thresholds can be changed live on the Matching
screen.

Within the Review Desk, expand **Research this match manually**, edit the title,
author, provider, or result limit, and select **Search Now**. The row remains
open while searching and reports whether the edited fields produced a confident
match, review-only candidates, no results, or an error. Candidates can be
selected and approved directly, or the item can be rejected without leaving the
review.

The persistent **Live activity** strip confirms every server-backed action,
keeps an elapsed timer running, disables the initiating button while work is in
flight, and retains the completion or error result. **Scan Review Tags** also
shows its live phase, current book title, and `completed / total` count while
Audiobookshelf and metadata-provider requests are still running.

For speed and rate-limit safety, optional Audiobookshelf-proxied providers such
as iTunes are disabled by default. Tested native Google is the automatic second
pass whenever ABS does not auto-match, and native Open Library is the third.
The primary provider gets one precise search; only an empty response can trigger
a cleaned/title-only retry. Leading track/disc numbers are removed before the
first query. Metadata search timeout is separate from the general ABS API
timeout and never uses general API retries. Provider failures follow the
run-local retry and circuit-breaker policy described above, then leave the item
available for Review.

Track prefixes with secondary indices, such as `01(3) Octopussy` and
`02 (7) Thunderball`, are reduced to the real title before searching and
scoring, while numeric titles such as `11(22)63` remain intact.

Series-number folder prefixes are parsed separately from real titles. For
example, `Pern 17 - The Masterharper of Pern` searches as
`The Masterharper of Pern`. Series metadata or a repeated distinctive series
word makes that cleanup immediate; otherwise the unmodified title is tried
first and the parsed title is only a fallback. Candidate cards display the
strategy that produced them, and quick-match mode uses an evidence-backed
parsed title when applicable.

## Publish the request portal on a custom domain

The Configuration screen can enable a separate request portal without exposing
the HeavyMLM dashboard on that hostname. Set the request domain, a portal title,
named requester accounts, and a per-client request limit there. Changes apply
immediately. Passwords are never written to `config.toml` in plain text;
MyAnonaSuite saves salted PBKDF2-SHA256 hashes. The old shared username/password
and access code remain available as compatibility options for upgraded installs.

Each named account has its own permissions. Ordinary accounts send requests to
the Requests inbox for review. Trusted accounts can be given **Auto-approve
requests**, which schedules valid releases immediately under the same format,
slot, ratio, and freeleech-wedge safeguards as manually approved requests. This
permission does not grant access to the HeavyMLM dashboard, configuration, or
other MyAnonaSuite modules. Removing an account or resetting its password
invalidates that account's existing portal login session.

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
Goodreads book URL to prefill its metadata and run the search. Submissions from
ordinary accounts enter the Requests inbox for approval. Submissions from
trusted auto-approve accounts are revalidated and scheduled immediately. Every
decision records the account that submitted it and remains visible in request
history.
