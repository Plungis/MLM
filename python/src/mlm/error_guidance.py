from __future__ import annotations

from typing import Any


def error_guidance(message: str) -> dict[str, Any]:
    """Turn a stored pipeline exception into useful operator guidance."""
    normalized = message.casefold()

    if "wedge reserve reached" in normalized:
        return {
            "title": "Freeleech wedge reserve reached",
            "reason": (
                "This release requires a wedge, but using one would cross the wedge "
                "buffer configured for MyAnonaSuite."
            ),
            "steps": [
                "Wait until you have more wedges, or lower the wedge buffer in "
                "Configuration.",
                "If ratio use is acceptable, change the source rule from wedge to "
                "try_wedge or all.",
                "Retry this release after changing the policy.",
            ],
            "component": "downloader",
        }

    if "wedge" in normalized:
        return {
            "title": "MyAnonamouse rejected the wedge request",
            "reason": (
                "The downloader asked MyAnonamouse to apply a freeleech wedge, but "
                "the tracker did not confirm it."
            ),
            "steps": [
                "Open Downloader diagnostics and inspect the wedge response.",
                "Confirm the account has an available wedge above the configured "
                "reserve.",
                "If ratio fallback is acceptable, enable Download if wedge fails "
                "in Configuration and retry.",
                "Retry once the tracker account and wedge balance are ready.",
            ],
            "component": "downloader",
        }

    if "no longer free" in normalized or "freeleech" in normalized:
        return {
            "title": "Release is no longer freeleech",
            "reason": (
                "The release stopped being free before it reached qBittorrent, and its "
                "current rule does not allow ratio-based downloading."
            ),
            "steps": [
                "Refresh the source list to look for a different freeleech release.",
                "Use a wedge-capable or ratio-capable grab rule if you still want "
                "this release.",
                "Retry after changing the rule or when the release becomes free again.",
            ],
            "component": "downloader",
        }

    if any(
        token in normalized
        for token in ("mam_id", "session check", "not authorized", "403 forbidden")
    ):
        return {
            "title": "MyAnonamouse session is not authorized",
            "reason": (
                "MyAnonaSuite could not authenticate the configured mam_id cookie with "
                "MyAnonamouse."
            ),
            "steps": [
                "Generate or copy a current API-session mam_id from MyAnonamouse.",
                "Replace mam_id in config.toml, then restart MyAnonaSuite.",
                "Open Diagnostics to confirm the session check succeeds before "
                "retrying.",
            ],
            "component": "downloader",
        }

    if any(
        token in normalized
        for token in ("qbittorrent", "/api/v2/", "connection refused", "connecterror")
    ):
        return {
            "title": "qBittorrent could not accept the release",
            "reason": (
                "MyAnonaSuite could not connect to qBittorrent or qBittorrent rejected "
                "the request."
            ),
            "steps": [
                "Confirm qBittorrent is running and its Web UI is enabled.",
                "Check the qBittorrent URL, username, and password in config.toml.",
                "Open Downloader diagnostics for the HTTP failure, then retry.",
            ],
            "component": "downloader",
        }

    if any(
        token in normalized
        for token in (
            "path_mapping",
            "save_path",
            "no such file",
            "cannot find the path",
        )
    ):
        return {
            "title": "Configured file path is unavailable",
            "reason": (
                "The organizer or torrent client reported a path that MyAnonaSuite "
                "cannot currently access."
            ),
            "steps": [
                "Confirm the drive or network share is mounted and readable.",
                "Check library_dir and qBittorrent path_mapping values in config.toml.",
                "Open Organizer diagnostics, correct the path, and run the organizer "
                "again.",
            ],
            "component": "organizer",
        }

    if "invalid size" in normalized:
        return {
            "title": "Source returned an unreadable size",
            "reason": (
                "A source list supplied a file-size format that this build could not "
                "parse."
            ),
            "steps": [
                "Refresh the list with the latest MyAnonaSuite build.",
                "Open Lists diagnostics and note the source and exact size value.",
                "If it repeats, keep this error and report the raw message for a "
                "parser fix.",
            ],
            "component": "lists",
        }

    if any(
        token in normalized
        for token in (
            "file placement",
            "source file is",
            "library destination",
            "size mismatch",
            "could not hardlink",
            "placed file could not be verified",
        )
    ):
        return {
            "title": "Library file placement failed",
            "reason": (
                "HeavyMLM could not safely place and verify every media file. The "
                "incomplete staging copy was removed, so Audiobookshelf will not see "
                "an empty book folder."
            ),
            "steps": [
                "Compare the recorded source and destination paths below.",
                "Confirm the source drive is mounted and the library drive has free "
                "space and write permission.",
                "Correct path_mapping or the library method if needed, then run "
                "Organize files again.",
            ],
            "component": "organizer",
        }

    return {
        "title": "Download processing failed",
        "reason": (
            "The release hit an unexpected failure. The exact message below is "
            "preserved "
            "so the failing service can be identified."
        ),
        "steps": [
            "Open Downloader diagnostics and inspect the entries at the same time "
            "as this error.",
            "Verify MyAnonamouse and qBittorrent are reachable, then retry once.",
            "If it repeats, keep the raw error and diagnostics details for "
            "troubleshooting.",
        ],
        "component": "downloader",
    }
