"""ABSidekick Audiobookshelf matching module."""

from .core import APP_VERSION as SOURCE_VERSION
from .service import ABSidekickService

__all__ = ["ABSidekickService", "SOURCE_VERSION"]
