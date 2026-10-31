from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OnboardingResult:
    username: str
    password: str
    display_name: str
    abs_url: str
    portal_url: str
    auto_approve: bool
    weekly_request_limit: int
    abs_status: dict[str, Any]
    portal_status: dict[str, Any]
    blurb: str


def generate_suggested_password() -> str:
    """Generate a clean, memorable, secure password for onboarding new readers."""
    words = [
        "Audio",
        "Book",
        "Chapter",
        "Novel",
        "Page",
        "Story",
        "Reader",
        "Listen",
        "Library",
        "Sound",
        "Echo",
        "Track",
    ]
    w1 = secrets.choice(words)
    w2 = secrets.choice(words)
    while w2 == w1:
        w2 = secrets.choice(words)
    num = secrets.randbelow(900) + 100
    special = secrets.choice(["!", "@", "#", "$", "%", "*"])
    return f"{w1}{w2}{num}{special}"


def generate_onboarding_blurb(
    *,
    username: str,
    password: str,
    display_name: str = "",
    abs_url: str = "",
    portal_url: str = "",
    auto_approve: bool = True,
    weekly_request_limit: int = 0,
) -> str:
    """Generate a comprehensive, ready-to-share onboarding text blurb."""
    friendly_name = display_name.strip() or username.strip()

    if auto_approve:
        approval_header = "⚡ Instant Auto-Approval"
        approval_body = (
            "Your book requests are pre-approved! As soon as you click Request,\n"
            "  our system automatically finds the release, downloads it, and\n"
            "  places it in your Audiobookshelf library."
        )
    else:
        approval_header = "⏳ Admin Approval"
        approval_body = (
            "Your book requests go to the library review inbox and will be\n"
            "  downloaded as soon as approved by the admin."
        )

    if weekly_request_limit > 0:
        quota_text = f"{weekly_request_limit} books per rolling 7 days"
    else:
        quota_text = "Unlimited books"

    abs_section = ""
    if abs_url:
        abs_section = f"""--------------------------------------------------
🎧 PART 1: HOW TO LISTEN & READ (Audiobookshelf & Absorb)
--------------------------------------------------
• Server URL: {abs_url}

• Mobile App Options:
  📱 iPhone / iPad (iOS):
     • Absorb (Recommended - on the App Store):
       https://apps.apple.com/app/absorb-for-audiobookshelf/id6477741369
     • Official Audiobookshelf (TestFlight Beta):
       https://testflight.apple.com/join/wiic9QIW
       (Or open {abs_url} in Safari and tap "Add to Home Screen")

  🤖 Android:
     • Official Audiobookshelf (on Google Play):
       https://play.google.com/store/apps/details?id=com.audiobookshelf.app
     • Absorb (on Google Play):
       https://play.google.com/store/apps/details?id=com.barnabas.absorb

• How to Connect:
  1. Install your preferred app (Absorb or Audiobookshelf) or open {abs_url}.
  2. Enter Server URL: {abs_url}
  3. Sign in with your username and password above.
• Features:
  ✓ Stream or download audiobooks & ebooks for offline listening/reading
  ✓ Sync playback progress seamlessly across phone, tablet, and browser
  ✓ Customizable speed controls (0.5x – 3x), sleep timer, and bookmarking
"""
    else:
        abs_section = """--------------------------------------------------
🎧 PART 1: HOW TO LISTEN & READ (Audiobookshelf & Absorb)
--------------------------------------------------
Ask your administrator for the Audiobookshelf server address.
"""

    portal_section = ""
    if portal_url:
        portal_section = f"""--------------------------------------------------
📖 PART 2: HOW TO REQUEST ANY BOOK (Request Portal)
--------------------------------------------------
• Request Portal: {portal_url}
• Approval Mode: {approval_header}
  {approval_body}
• Request Quota: {quota_text}
• How to Request a Book:
  1. Open {portal_url} in any web browser.
  2. Log in with your username and password.
  3. Search for any audiobook, series, or ebook by title or author.
  4. Click "Request Book".
  5. The server automatically searches private trackers, downloads the
     highest quality release, and imports it directly into Audiobookshelf!
"""
    else:
        portal_section = """--------------------------------------------------
📖 PART 2: HOW TO REQUEST ANY BOOK (Request Portal)
--------------------------------------------------
Ask your administrator for the book request portal link.
"""

    return f"""📚 Welcome to the Library!
==================================================

Hey {friendly_name}! Your accounts are ready.
Here is everything you need to start listening and reading, plus how to
request any audiobooks or ebooks you want added.

🔑 YOUR LOGIN CREDENTIALS
• Username: {username}
• Password: {password}
(Use these same credentials for both Audiobookshelf and the Request Portal)

{abs_section}
{portal_section}
--------------------------------------------------
💡 Need help or have questions? Just reach out!
Enjoy listening and reading!
"""
