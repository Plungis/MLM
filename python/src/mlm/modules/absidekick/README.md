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

## Boundaries

MyAnonaSuite owns routing, local-only access checks, static delivery, and the
shared layout. ABSidekick owns all Audiobookshelf calls and never uses the MaM
session. Blocking ABS API operations run off the FastAPI event loop so the rest
of the suite remains responsive during previews and review scans.
