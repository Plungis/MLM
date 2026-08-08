from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, TypeVar

POINTS_PER_BLOCK = 25_000
GB_PER_BLOCK = 50
MAX_UPLOAD_BLOCKS_PER_RUN = 3
MAX_UPLOAD_POINTS_PER_RUN = POINTS_PER_BLOCK * MAX_UPLOAD_BLOCKS_PER_RUN
FL_WEDGE_COST = 50_000
VIP_RENEW_DAYS = 83
MIN_INTERVAL_MINUTES = 2
MAX_POINTS_BUFFER = 25_000


@dataclass
class Settings:
    buy_vip: bool = True
    buy_upload_credit: bool = True
    alternate_fl_upload: bool = False
    alternate_next_purchase: str = "freeleech_wedge"
    fl_only: bool = False
    theme: str = "ember"
    points_buffer: int = 10_000
    next_run_delay_minutes: int = 15

    def normalize(self) -> None:
        self.theme = str(self.theme).strip().casefold()
        if self.theme not in {"green", "ember", "modern", "mouse"}:
            self.theme = "ember"
        self.points_buffer = max(0, min(MAX_POINTS_BUFFER, int(self.points_buffer)))
        self.next_run_delay_minutes = max(
            MIN_INTERVAL_MINUTES, int(self.next_run_delay_minutes)
        )
        if self.alternate_next_purchase not in {
            "freeleech_wedge",
            "upload_credit",
        }:
            self.alternate_next_purchase = "freeleech_wedge"
        if self.fl_only:
            self.buy_upload_credit = False
            self.alternate_fl_upload = False
        elif self.alternate_fl_upload:
            self.buy_upload_credit = True


@dataclass
class Totals:
    cumulative_upload_gb: int = 0
    cumulative_points_spent: int = 0
    cumulative_freeleech_wedges: int = 0
    cumulative_freeleech_points_spent: int = 0
    cumulative_vip_purchases: int = 0


@dataclass
class UserSummary:
    username: str = "N/A"
    vip_expires: str = "N/A"
    downloaded: str = "N/A"
    uploaded: str = "N/A"
    ratio: str = "N/A"


T = TypeVar("T")


def dataclass_from_dict(cls: type[T], values: dict[str, Any] | None) -> T:
    defaults = asdict(cls())  # type: ignore[call-arg]
    allowed = {item.name for item in fields(cls)}
    defaults.update(
        {key: value for key, value in (values or {}).items() if key in allowed}
    )
    return cls(**defaults)
