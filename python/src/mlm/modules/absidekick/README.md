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
Review Desk remains independent and available.

The provider is fail-closed. Saving a key clears any previous validation, and
the Google code path refuses to make an outbound request until **Test & Enable**
receives a successful response from the official Books API. Removing or
replacing the key disables the provider again. The Config screen contains the
Google Cloud project, Books API enablement, credential, API restriction, and
optional stable-IP restriction steps.

The Review Desk also supports reviewer-controlled searches by title, optional
author, and metadata provider. Manual results are rescored by `core.py` against
the canonical Audiobookshelf item and use the normal review approval path.

The automatic matcher is intentionally evidence-aware. It performs a precise
title-and-author search first, broadens the query only after an empty result,
and can optionally consult configured fallback providers. Missing provider fields are excluded
from the weighted score. Contradictory identifiers, authors, series positions,
collection status, duration, or a close runner-up prevent automatic writes and
are recorded as explicit Review Desk reasons. This policy and its thresholds
live under the module's `matching` settings rather than in shared suite code.

Search execution is deliberately bounded: numbering prefixes are cleaned,
additional queries run only after zero results, automatic fallback providers
are opt-in, and search requests use a short no-retry timeout. A timed-out
provider is disabled on that `ABSClient` for the rest of the job. Provider
failures are attached to preview/review rows and written to the module log.

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
