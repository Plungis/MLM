# MAM-Spender module

This directory owns MAM-Spender behavior. It should not import HeavyMLM jobs,
templates, or downloader policy. Cross-module functionality belongs behind a
small interface in the suite layer.

## File map

- `models.py` contains normalized module settings and public state models.
- `service.py` owns scheduling, MaM reads, purchase decisions, verification,
  logging, and the public service API used by the web layer.
- `storage.py` owns MAM-Spender-specific SQLite persistence and migrations.
- `templates/modules/mam_spender/_purchase_settings.html` is the reusable
  purchase-policy editor shown on both the Dashboard and Config pages.
- `templates/mam_spender.html` composes the module pages inside the shared
  MyAnonaSuite shell.
- `static/mam-spender.js` owns live module rendering and controls.
- MAM-Spender styles are grouped together under the clearly marked
  `MAM-Spender` section in `static/app.css` while the suite has one stylesheet.
- `tests/test_mam_spender.py` covers module policy and MaM purchase behavior;
  dedicated page routing is covered by `tests/test_web.py`.

## Boundaries

MAM-Spender shares only the authenticated MaM client, repository connection,
listener, and suite navigation. Its settings are persisted as module state and
apply without restarting. Host and port stay suite-wide because MyAnonaSuite is
one web process.

When adding a setting, update `Settings`, normalization, `update_settings`, the
shared purchase-settings partial when it is a day-to-day control, and a focused
test. Authentication, migration, and uncommon controls belong on the Config
page; frequently adjusted purchase controls may also appear on the Dashboard
through the shared partial.
