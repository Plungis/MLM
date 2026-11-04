# ABSidekick module

This directory owns Audiobookshelf matching and review behavior. It does not
import HeavyMLM download policy or MAM-Spender purchase behavior.

## File map

- `core.py` is the matching engine ported from `Plungis/absidekick-beta`
  Beta V.91.1. It owns filtering, scoring, ABS API calls, metadata/tag writes,
  job execution, previews, and review decisions.
- `service.py` adapts that engine to the MyAnonaSuite process. It owns module
  settings, the current job, review persistence, and the small API used by the
  suite web layer.
- `templates/absidekick.html` preserves the source controls and review desk
  inside the shared suite shell.
- `static/absidekick.js` owns module interaction and talks only to
  `/api/absidekick/*`.
- `static/absidekick.css` scopes ABSidekick's detailed workspace styles under
  `.absidekick-module`, so module styling cannot leak into the suite shell.

## Persistence

Runtime state lives beside the selected SQLite database in the
`absidekick/` directory:

- `settings.json` stores module settings and stores the ABS token by default.
  Disable **Save URL, library, and API token** only when session-only behavior
  is intentional. The UI never reads the saved token back into the browser.
- `review_state.json` remembers approved and rejected review items.

## Native Google Books provider

Google Books searches are made directly by `core.py`; they are not proxied
through Audiobookshelf. The optional API key is stored only in this module's
private `settings.json` and is restored after process restarts. Public settings
expose only whether a key exists,
whether its fingerprint matches the last successful test, the validation time,
and a safe error message. Neither the key nor its fingerprint is returned to
the browser. A tested key automatically enables Google as the second-stage
provider for previews, initial jobs, and Review Desk scans: the configured
Audiobookshelf provider runs first, and Google is contacted only if that
provider does not produce a confident match. Manual Google searching in the
Review Desk remains independent and available. Google timeouts, rate limits,
and 5xx responses receive one immediate retry. One exhausted transient search
does not disable Google; three consecutive failures open a circuit breaker for
that run. Authentication and API-configuration failures disable Google
immediately. The next successful search resets the transient failure count.

The provider is fail-closed. Saving a key clears any previous validation, and
the Google code path refuses to make an outbound request until **Test & Enable**
receives a successful response from the official Books API. Removing or
replacing the key disables the provider again. The Config screen contains the
Google Cloud project, Books API enablement, credential, API restriction, and
optional stable-IP restriction steps.

## Native Open Library provider

Open Library searches are also made directly by `core.py`, using the official
`/search.json` API rather than Audiobookshelf's legacy OpenLib provider. It is
enabled by default as the third automatic stage: the configured Audiobookshelf
provider runs first, a tested Google key runs second, and Open Library runs only
when neither earlier stage passes the normal auto-match policy. The same native
provider is available in Review Desk manual search.

No API key is needed. Config accepts an optional contact email for the
application-identification headers requested by Open Library. Requests are
limited to three per second when identified and one per second when anonymous,
and identical searches are cached for the life of the `ABSClient`. Only the
fields used by candidate scoring and display are requested. Returned work keys,
titles, authors, first publication years, ISBNs, publishers, languages, edition
counts, and cover IDs are converted to the common candidate model.

Open Library timeouts, rate limits, and 5xx responses get one immediate retry;
three consecutive exhausted transient searches open a run-local circuit
breaker. A non-transient rejection disables the provider for that run and
records the underlying error in search diagnostics. Turning the provider off
in Config prevents both automatic and manual Open Library requests.

The Review Desk also supports reviewer-controlled searches by title, optional
author, and metadata provider. Manual results are rescored by `core.py` against
the canonical Audiobookshelf item and use the normal review approval path.

The automatic matcher is intentionally evidence-aware. It derives a bounded,
ranked set of title variants before searching. Strongly evidenced track,
series, and nested release prefixes are peeled in layers—for example,
`Dragonriders of Pern #3 - Pern08-Nerilka's Story` becomes `Nerilka's Story`,
then the intermediate and original forms remain available as fallbacks. Weak or
ambiguous prefixes keep the original title first, so names such as
`Catch 22 - A Novel` are not destructively cleaned. The matcher stops as soon
as a variant passes the normal auto-match policy, then uses native Google and
Open Library stages before optional configured fallback providers. Every
provider attempt records the exact title variant and author it searched, and
labels author-free broadening as `title only`. Decimal/lettered positions,
multi-volume ranges, compact track numbers, bracketed release groups, and disc
suffixes are recognized while numeric work titles remain intact. Identical
current/embedded title variants are collapsed and use their paired embedded
author rather than sending duplicate queries. Missing provider fields are
excluded from the weighted score. Contradictory authors, series
positions, collection status, or a close runner-up prevent automatic writes and
are recorded as explicit Review Desk reasons. When an ASIN or ISBN matches
exactly, a different secondary identifier or stored duration is treated as a
non-blocking edition note. An ISBN mismatch alone is also informational when
both title and author are present and strongly agree. ASIN, duration,
series-position, collection, weak/missing-author, and winner-margin conflicts
remain blocking without an exact identifier. This policy and its thresholds
live under the module's `matching` settings rather than in shared suite code.

The current ABS title and the final folder name are both title evidence. When a
folder or parsed search title clearly matches the candidate but the current ABS
title is short, incomplete, or incorrect, that disagreement is displayed as a
non-blocking match note. It no longer vetoes an otherwise passing score, author,
signal-count, and winner-margin decision. Author, series-position, collection,
and competing-work conflicts remain blocking. Identifier and duration conflicts
remain blocking unless another identifier matches exactly, except for the
strong-title-and-author ISBN rule above.

Before metadata-patch mode calls Audiobookshelf, author names are normalized and
deduplicated case-insensitively. If the candidate's normalized author set is
already the same as the book's current author set, the author field is omitted
even when metadata overwrite is enabled. This avoids Audiobookshelf 2.36.0's
`bookAuthors.bookId, bookAuthors.authorId` unique-constraint crash path without
preventing a genuinely different author set from being applied.

## Embedded file metadata (opt-in)

`matching.useEmbeddedFileMetadata` is disabled by default. When enabled,
ABSidekick uses the `metaTags` and filenames Audiobookshelf already reports for
the audio files associated with each library item; MyAnonaSuite does not open or
modify the media files. Album/title, artist/album artist, composer, series,
series sequence, year, ASIN, ISBN, publisher, and language are normalized into a
consensus evidence record. Album is preferred over per-track title because MP3
track titles are commonly chapter names. Repeated filenames can also supply a
title after track counters and clearly numbered release prefixes are removed. A
multi-file value is accepted only when at least half of the files agree, and
chapter/part/track-only names are rejected.

The embedded title, filename title, and author are tried as explicit
primary-provider queries and are included as alternate matching evidence.
Generic ABS placeholders such as `Star Wars Book 58` also try a distinct folder
basename before the placeholder query. Current ABS metadata and file evidence
are both retained; file evidence never directly overwrites ABS metadata.
The live job log, preview, search path, and Review Desk show how many files had
tags and every field that was used. If the library-items response omits audio
files, the module loads the full ABS item once. That optional lookup fails open,
records an actionable warning, and continues using normal ABS metadata.
List-shaped narrator data returned by Audiobookshelf is normalized before
deduplication and scoring.

## Series repair

`matching.repairSeries` is enabled by default and is independent of the broad
`overwriteMetadata` switch. After a match, ABSidekick writes Audiobookshelf's
structured series array with both the series name and its sequence. Audible
search responses use `series` for the series name while stored ABS metadata uses
`name`; the module accepts both shapes, preserves multiple series memberships,
deduplicates repeated entries, and retains decimal sequence values.

Provider-supplied structured series data has priority. If it is absent, opted-in
embedded file tags may supply the series name and number; a clearly structured
provider subtitle such as `Sword of Truth, Book 1` is the final fallback.
Existing sequence data is preserved when the provider supplies the same series
without a number. Disable **Repair ABS series names and book numbers** in Match
Policy to retain the older fill-empty-only behavior.

Search execution is deliberately bounded: no more than four evidence-ranked
title variants are produced, duplicate queries are suppressed, optional ABS
fallback providers are opt-in, and search requests use a short timeout. Native
Google and Open Library use a one-retry/three-strike transient-error policy;
other timed-out providers are disabled on that `ABSClient` for the rest of the
job. Provider failures are attached to preview/review rows and written to the
module log.

The module UI has a persistent Live Activity strip for every server-backed
control. It owns button busy/disabled states, elapsed time, and persistent
success or actionable failure results. Review-tag scans additionally publish
thread-safe backend progress through `/api/absidekick/activity`, including the
loading/search phase, current title, and completed/total count. A refreshed UI
can resume observing an in-progress scan instead of losing its status.

## Boundaries

MyAnonaSuite owns routing, local-only access checks, static delivery, and the
shared layout. ABSidekick owns all Audiobookshelf calls and never uses the MaM
session. Blocking ABS API operations run off the FastAPI event loop so the rest
of the suite remains responsive during previews and review scans.
