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

- `settings.json` stores module settings and stores the ABS token only when
  **Remember URL/library/token** is enabled.
- `review_state.json` remembers approved and rejected review items.

## Native Google Books provider

Google Books searches are made directly by `core.py`; they are not proxied
through Audiobookshelf. The optional API key is stored only in this module's
private `settings.json`. Public settings expose only whether a key exists,
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

The automatic matcher is intentionally evidence-aware. It performs a precise
title-and-author search first, broadens the query only after an empty result,
then uses the native Google and Open Library stages before optional configured
fallback providers. Missing provider fields are excluded
from the weighted score. Contradictory identifiers, authors, series positions,
collection status, duration, or a close runner-up prevent automatic writes and
are recorded as explicit Review Desk reasons. This policy and its thresholds
live under the module's `matching` settings rather than in shared suite code.

The current ABS title and the final folder name are both title evidence. When a
folder or parsed search title clearly matches the candidate but the current ABS
title is short, incomplete, or incorrect, that disagreement is displayed as a
non-blocking match note. It no longer vetoes an otherwise passing score, author,
signal-count, and winner-margin decision. Identifier, author, series-position,
collection, duration, and competing-work conflicts remain blocking.

Before metadata-patch mode calls Audiobookshelf, author names are normalized and
deduplicated case-insensitively. If the candidate's normalized author set is
already the same as the book's current author set, the author field is omitted
even when metadata overwrite is enabled. This avoids Audiobookshelf 2.36.0's
`bookAuthors.bookId, bookAuthors.authorId` unique-constraint crash path without
preventing a genuinely different author set from being applied.

Search execution is deliberately bounded: numbering prefixes are cleaned,
additional primary-provider queries run only after zero results, optional ABS
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
